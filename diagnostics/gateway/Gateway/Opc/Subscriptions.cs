using System.Globalization;
using System.Text.Json;
using Gateway.Ingest;
using Opc.Ua;
using Opc.Ua.Client;

namespace Gateway.Opc;

/// <summary>
/// Live ingest: two variables and one event stream, per §4.2's three kinds of traffic.
/// </summary>
public sealed class Subscriptions
{
    /// <summary>
    /// The decoding contract. An event notification arrives as a positional EventFieldList,
    /// so this order IS the wire format — it is stated here independently of the plant rather
    /// than derived from it, because a check that moves when the thing it checks moves proves
    /// nothing. Task 10's history reader must use the identical filter.
    /// </summary>
    public static readonly string[] InspectionEventFields =
    [
        "Time", "AssemblySerial", "Disposition", "DefectClass", "Confidence", "ModelVersion", "Image",
    ];

    private const string ImageField = "Image";

    private readonly GatewayOptions _options;
    private readonly Func<IngestRecord, Task> _onRecord;

    public int OverflowCount { get; private set; }

    public Subscriptions(GatewayOptions options, Func<IngestRecord, Task> onRecord)
    {
        _options = options;
        _onRecord = onRecord;
    }

    public async Task<Subscription> StartAsync(
        ISession session, AddressSpace space, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);
        ArgumentNullException.ThrowIfNull(space);

        var subscription = new Subscription(session.DefaultSubscription)
        {
            DisplayName = "machine-agent S3",
            PublishingEnabled = true,
            PublishingInterval = _options.PublishingIntervalMs,
            KeepAliveCount = 10,
            LifetimeCount = 100,
            MaxNotificationsPerPublish = 0,
        };
        session.AddSubscription(subscription);
        await subscription.CreateAsync(ct).ConfigureAwait(false);

        // TaktTime is a noisy float, so a deadband is meaningful.
        var takt = new MonitoredItem(subscription.DefaultItem)
        {
            StartNodeId = space.TaktNodeId,
            AttributeId = Attributes.Value,
            DisplayName = "S3.TaktTime",
            SamplingInterval = _options.SamplingIntervalMs,
            QueueSize = _options.QueueSize,
            DiscardOldest = true,
            Filter = new DataChangeFilter
            {
                Trigger = DataChangeTrigger.StatusValue,
                DeadbandType = (uint)DeadbandType.Absolute,
                DeadbandValue = _options.TaktDeadband,
            },
        };
        takt.Notification += OnDataChange;

        // PartCount is monotonic. A deadband here would silently lose parts, so there is
        // none. §5.1 makes deadbands the gateway's job but never says they are per-signal;
        // this pair settles that they must be.
        var partCount = new MonitoredItem(subscription.DefaultItem)
        {
            StartNodeId = space.PartCountNodeId,
            AttributeId = Attributes.Value,
            DisplayName = "S3.PartCount",
            SamplingInterval = _options.SamplingIntervalMs,
            QueueSize = _options.QueueSize,
            DiscardOldest = false,   // prefer failing loudly over dropping a count
            Filter = null,
        };
        partCount.Notification += OnDataChange;

        var events = new MonitoredItem(subscription.DefaultItem)
        {
            StartNodeId = space.S3NodeId,
            AttributeId = Attributes.EventNotifier,
            NodeClass = NodeClass.Object,
            DisplayName = "S3.InspectionResult",
            SamplingInterval = 0,
            QueueSize = _options.EventQueueSize,
            Filter = BuildInspectionFilter(),
        };
        events.Notification += OnEvent;

        subscription.AddItems([takt, partCount, events]);
        await subscription.ApplyChangesAsync(ct).ConfigureAwait(false);
        return subscription;
    }

    private static EventFilter BuildInspectionFilter()
    {
        var select = new SimpleAttributeOperandCollection();
        foreach (var field in InspectionEventFields)
        {
            select.Add(new SimpleAttributeOperand
            {
                AttributeId = Attributes.Value,
                TypeDefinitionId = ObjectTypeIds.BaseEventType,
                BrowsePath = [new QualifiedName(field)],
            });
        }

        return new EventFilter { SelectClauses = select };
    }

    private void OnDataChange(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not MonitoredItemNotification notification)
        {
            return;
        }

        // §4.4: the server sets the overflow bit when it dropped notifications.
        // DiscardOldest=false replaces the NEWEST value — it is not a lossless setting.
        // Losslessness comes from an adequate queue plus a fast publishing interval, with
        // this bit as the honest detector when that fails. Persisting the gap as a row is
        // Task 9's, once there is a table to put it in.
        if (notification.Value.StatusCode.Overflow)
        {
            OverflowCount++;
        }

        var payload = JsonSerializer.Serialize(new Dictionary<string, string?>(StringComparer.Ordinal)
        {
            ["DisplayName"] = item.DisplayName,
            ["Value"] = Convert.ToString(notification.Value.Value, CultureInfo.InvariantCulture),
        });

        _ = _onRecord(new IngestRecord(
            Kind: "datachange",
            NodeId: item.StartNodeId.ToString(),
            SourceTs: notification.Value.SourceTimestamp,
            ServerTs: notification.Value.ServerTimestamp,
            StatusCode: notification.Value.StatusCode.Code,
            PayloadJson: payload,
            ImageBytes: null));
    }

    private void OnEvent(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not EventFieldList fields)
        {
            return;
        }

        byte[]? image = null;
        var payload = new Dictionary<string, string?>(StringComparer.Ordinal);
        var sourceTs = DateTime.UtcNow;

        for (var i = 0; i < InspectionEventFields.Length && i < fields.EventFields.Count; i++)
        {
            var name = InspectionEventFields[i];
            var value = fields.EventFields[i].Value;

            if (name == ImageField)
            {
                // Rejects only; a good part carries no image and that is not a data gap (§3.4).
                // The server sends an empty ByteString rather than a null one for a good part,
                // and an empty byte[] is not "no image" — stored as-is it would put a row in
                // inspection_images for every good part, which is the thing §3.4 forbids.
                image = value as byte[] is { Length: > 0 } bytes ? bytes : null;
                continue;
            }

            if (name == "Time" && value is DateTime time)
            {
                sourceTs = time;
            }

            payload[name] = Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        _ = _onRecord(new IngestRecord(
            Kind: "event",
            NodeId: item.StartNodeId.ToString(),
            SourceTs: sourceTs,
            ServerTs: DateTime.UtcNow,
            StatusCode: 0,
            PayloadJson: JsonSerializer.Serialize(payload),
            ImageBytes: image));
    }
}

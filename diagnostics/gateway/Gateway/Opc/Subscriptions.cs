using System.Collections.Concurrent;
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

    /// <summary>
    /// The last SourceTimestamp delivered per monitored item. The overflow bit means values
    /// were dropped between the previous delivery and this one, so this is what turns "some
    /// were lost" into the interval §4.4 asks a gap marker to name.
    /// </summary>
    private readonly ConcurrentDictionary<string, DateTime> _lastSourceTs =
        new(StringComparer.Ordinal);

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
            DisplayName = "TaktTime",
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
            DisplayName = "PartCount",
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

    public static EventFilter BuildInspectionFilter()
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

    /// <summary>
    /// One decoder for a variable sample, used by the live subscription and by the history
    /// backfill. Two decoders would be two chances to disagree about the payload shape the
    /// writer depends on.
    /// </summary>
    public static IngestRecord ToDataChangeRecord(string signal, string nodeId, DataValue value)
    {
        ArgumentNullException.ThrowIfNull(value);

        // Shape is the contract with PostgresWriter: station, signal, and a numeric value,
        // because signals.value is DOUBLE PRECISION and a stringified number would land as
        // text that only fails at insert time.
        var payload = JsonSerializer.Serialize(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["Station"] = AddressSpace.StationCode,
            ["Signal"] = signal,
            ["Value"] = Convert.ToDouble(value.Value, CultureInfo.InvariantCulture),
        });

        return new IngestRecord(
            Kind: "datachange",
            NodeId: nodeId,
            SourceTs: value.SourceTimestamp,
            ServerTs: value.ServerTimestamp,
            StatusCode: value.StatusCode.Code,
            PayloadJson: payload,
            ImageBytes: null);
    }

    /// <summary>One decoder for an inspection event, shared for the same reason.</summary>
    public static IngestRecord ToEventRecord(string nodeId, IList<Variant> fields)
    {
        ArgumentNullException.ThrowIfNull(fields);

        byte[]? image = null;
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["Station"] = AddressSpace.StationCode,
        };
        var sourceTs = DateTime.UtcNow;

        for (var i = 0; i < InspectionEventFields.Length && i < fields.Count; i++)
        {
            var name = InspectionEventFields[i];
            var value = fields[i].Value;

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

            if (name == "Confidence" && value is not null)
            {
                payload[name] = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                continue;
            }

            // An empty string is not a value. A good part carries no defect class, and the
            // server sends "" rather than null for it — stored as-is it becomes a defect class
            // whose name is empty, and every breakdown then reports good parts as a defect.
            // Same shape as the empty ByteString that would have given every good part an
            // image row: absent and empty are different claims.
            var text = Convert.ToString(value, CultureInfo.InvariantCulture);
            payload[name] = string.IsNullOrEmpty(text) ? null : text;
        }

        return new IngestRecord(
            Kind: "event",
            NodeId: nodeId,
            SourceTs: sourceTs,
            ServerTs: DateTime.UtcNow,
            StatusCode: 0,
            PayloadJson: JsonSerializer.Serialize(payload),
            ImageBytes: image);
    }

    private void OnDataChange(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not MonitoredItemNotification notification)
        {
            return;
        }

        var nodeId = item.StartNodeId.ToString();
        var sourceTs = notification.Value.SourceTimestamp;

        // §4.4: the server sets the overflow bit when it dropped notifications.
        // DiscardOldest=false replaces the NEWEST value — it is not a lossless setting.
        // Losslessness comes from an adequate queue plus a fast publishing interval, with
        // this bit as the honest detector when that fails. A counter alone was not enough:
        // /status forgets it on restart and no query can ask which minutes are missing, so
        // the loss goes in as a row through the same queue as everything else.
        if (notification.Value.StatusCode.Overflow)
        {
            OverflowCount++;

            var (from, reason) = _lastSourceTs.TryGetValue(nodeId, out var previous)
                ? (previous, "subscription_overflow")

                // Overflow on the first value this item ever delivered: nothing establishes
                // where the dropped run began. One publishing interval is the shortest it
                // could have been, and the reason says so rather than presenting a lower
                // bound as the extent.
                : (sourceTs.AddMilliseconds(-_options.PublishingIntervalMs),
                   "subscription_overflow_lower_bound");

            _ = _onRecord(PostgresWriter.GapRecord(from, sourceTs, reason, nodeId));
        }

        _lastSourceTs[nodeId] = sourceTs;
        _ = _onRecord(ToDataChangeRecord(item.DisplayName, nodeId, notification.Value));
    }

    private void OnEvent(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not EventFieldList fields)
        {
            return;
        }

        _ = _onRecord(ToEventRecord(item.StartNodeId.ToString(), fields.EventFields));
    }
}

using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using Gateway.Ingest;
using Opc.Ua;
using Opc.Ua.Client;

namespace Gateway.Opc;

/// <summary>
/// What a sample belongs to. A station signal and a buffer level are different payload shapes
/// to <see cref="PostgresWriter"/>: a buffer sits between two stations and belongs to neither,
/// so its records carry no Station key at all and asking one for a station would force it to
/// pick a side.
/// </summary>
public enum StreamOwner
{
    Station,
    Buffer,
}

/// <summary>Which stream one sample came from — the ingest payload's identity.</summary>
public sealed record StreamKey(StreamOwner Owner, string Code, string Signal);

/// <summary>
/// Live ingest across the discovered topology, per §4.2's three kinds of traffic. Every
/// browsed variable becomes a monitored item; what varies between them is the deadband, and
/// that comes from the mounted <see cref="SignalPolicy"/> rather than from this file.
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
    private const string EventStream = "InspectionResult";
    private const string BufferLevelSignal = "Level";

    private readonly GatewayOptions _options;
    private readonly SignalPolicy _policy;
    private readonly Func<IngestRecord, Task> _onRecord;

    /// <summary>
    /// The last SourceTimestamp delivered per monitored item, for every stream rather than
    /// the two M1 had. The overflow bit means values were dropped between the previous
    /// delivery and this one, so this is what turns "some were lost" into the interval §4.4
    /// asks a gap marker to name.
    /// </summary>
    private readonly ConcurrentDictionary<string, DateTime> _lastSourceTs =
        new(StringComparer.Ordinal);

    public int OverflowCount { get; private set; }

    public Subscriptions(
        GatewayOptions options, SignalPolicy policy, Func<IngestRecord, Task> onRecord)
    {
        _options = options;
        _policy = policy;
        _onRecord = onRecord;
    }

    public async Task<Subscription> StartAsync(
        ISession session, AddressSpace space, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);
        ArgumentNullException.ThrowIfNull(space);

        var subscription = new Subscription(session.DefaultSubscription)
        {
            DisplayName = "machine-agent line",
            PublishingEnabled = true,
            PublishingInterval = _options.PublishingIntervalMs,
            KeepAliveCount = 10,
            LifetimeCount = 100,
            MaxNotificationsPerPublish = 0,
        };
        session.AddSubscription(subscription);
        await subscription.CreateAsync(ct).ConfigureAwait(false);

        var items = new List<MonitoredItem>();
        foreach (var station in space.Stations)
        {
            foreach (var signal in station.Signals)
            {
                AddDataItem(
                    items, subscription,
                    new StreamKey(StreamOwner.Station, station.Code, signal.Name),
                    signal.NodeId, _policy.For(signal.Name, signal.Type));
            }

            if (station.EmitsEvents)
            {
                var events = new MonitoredItem(subscription.DefaultItem)
                {
                    StartNodeId = station.NodeId,
                    AttributeId = Attributes.EventNotifier,
                    NodeClass = NodeClass.Object,
                    DisplayName = $"{station.Code}.{EventStream}",
                    Handle = new StreamKey(StreamOwner.Station, station.Code, EventStream),
                    SamplingInterval = 0,
                    QueueSize = _options.EventQueueSize,
                    Filter = BuildInspectionFilter(),
                };
                events.Notification += OnEvent;
                items.Add(events);
            }
        }

        foreach (var buffer in space.Buffers)
        {
            AddDataItem(
                items, subscription,
                new StreamKey(StreamOwner.Buffer, buffer.Code, BufferLevelSignal),
                buffer.LevelNodeId, _policy.For(BufferLevelSignal, buffer.LevelType));
        }

        subscription.AddItems(items);
        await subscription.ApplyChangesAsync(ct).ConfigureAwait(false);

        // ApplyChanges reports per-item status and the SDK does not raise on a rejected one,
        // so an item the server refused — a filter it will not accept, a node it will not
        // monitor — would leave that stream silently unsubscribed. That is the same loss the
        // policy's fail-open default exists to prevent, one layer down.
        var refused = items
            .Where(item => !item.Created)
            .Select(item => $"{item.DisplayName} ({item.Status.Error})")
            .ToList();
        if (refused.Count > 0)
        {
            throw new ServiceResultException(
                StatusCodes.BadMonitoredItemIdInvalid,
                $"the server refused {refused.Count} of {items.Count} monitored items, and those "
                + $"streams would be lost silently: {string.Join("; ", refused)}");
        }

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
    public static IngestRecord ToDataChangeRecord(StreamKey key, string nodeId, DataValue value)
    {
        ArgumentNullException.ThrowIfNull(key);
        ArgumentNullException.ThrowIfNull(value);

        // Shape is the contract with PostgresWriter, which routes on it: a station signal
        // carries Station, a buffer level carries Buffer, and neither carries the other.
        var payload = JsonSerializer.Serialize(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            [key.Owner == StreamOwner.Station ? "Station" : "Buffer"] = key.Code,
            ["Signal"] = key.Signal,

            // By the value's own type, not by the signal's name. State and StateReason are
            // strings and signals.value is DOUBLE PRECISION, so they are different streams
            // rather than a special case of one — and an unforeseen string signal lands in
            // raw_events truthfully and fails its derivation loudly, rather than being
            // converted to a number that was never sent.
            ["Value"] = value.Value is string text
                ? text
                : Convert.ToDouble(value.Value, CultureInfo.InvariantCulture),
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
    public static IngestRecord ToEventRecord(
        string station, string nodeId, IList<Variant> fields)
    {
        ArgumentNullException.ThrowIfNull(fields);

        byte[]? image = null;
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["Station"] = station,
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

    private void AddDataItem(
        List<MonitoredItem> items, Subscription subscription, StreamKey key, NodeId node,
        SignalRule rule)
    {
        if (!rule.Subscribe)
        {
            return;
        }

        var item = new MonitoredItem(subscription.DefaultItem)
        {
            StartNodeId = node,
            AttributeId = Attributes.Value,
            DisplayName = $"{key.Code}.{key.Signal}",
            Handle = key,
            SamplingInterval = _options.SamplingIntervalMs,
            QueueSize = _options.QueueSize,

            // A deadbanded stream is a noisy float whose older samples say nothing the newer
            // ones do not; everything else here is a counter, a level or a PackML state, where
            // dropping the oldest queued value loses a part, a transition or a move. §4.4's
            // overflow bit is then the honest report, which DiscardOldest=true would suppress.
            DiscardOldest = rule.Deadband is not null,
            Filter = rule.Deadband is { } deadband
                ? new DataChangeFilter
                {
                    Trigger = DataChangeTrigger.StatusValue,
                    DeadbandType = (uint)DeadbandType.Absolute,
                    DeadbandValue = deadband,
                }
                : null,
        };

        item.Notification += OnDataChange;
        items.Add(item);
    }

    /// <summary>
    /// Every item this class creates is tagged with the stream it carries, and nothing else
    /// adds items to this subscription — an untagged notification is a bug in this file, not a
    /// condition to absorb quietly into a record with no owner.
    /// </summary>
    private static StreamKey KeyOf(MonitoredItem item) =>
        item.Handle as StreamKey
        ?? throw new InvalidOperationException(
            $"monitored item '{item.DisplayName}' carries no stream key");

    private void OnDataChange(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not MonitoredItemNotification notification)
        {
            return;
        }

        var key = KeyOf(item);
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
        _ = _onRecord(ToDataChangeRecord(key, nodeId, notification.Value));
    }

    private void OnEvent(MonitoredItem item, MonitoredItemNotificationEventArgs e)
    {
        if (e.NotificationValue is not EventFieldList fields)
        {
            return;
        }

        var key = KeyOf(item);
        _ = _onRecord(
            ToEventRecord(key.Code, item.StartNodeId.ToString(), fields.EventFields));
    }
}

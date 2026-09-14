using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using Gateway.Ingest;
using Microsoft.Extensions.Logging;
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
public sealed partial class Subscriptions
{
    /// <summary>
    /// How a station's event stream is named, in the subscription and in the backfill ledger
    /// alike. Not a variable in the address space — event history hangs off the emitting
    /// station node itself — so this names the stream rather than a signal.
    ///
    /// <para>Plural, and no longer "InspectionResult": four stations publish events now and
    /// five types ride the four streams, so naming any of them after one type would be wrong
    /// about three of them. It also spells the stream the way the plant's own ledger does,
    /// which is what lets the two counts be read side by side.</para>
    /// </summary>
    public const string EventStream = "Events";

    /// <summary>
    /// The one historised variable a buffer has, and the one signal name that routes to a
    /// buffer rather than to a station.
    ///
    /// <para>The single declaration of it. Discovery browses for it, the subscription and the
    /// backfill name their streams with it, the writer routes on it and the reconciliation
    /// counts against a different table because of it — five readers of one string, and while
    /// each held its own copy the backfill and the reconciler were free to disagree about
    /// which stream a buffer level even is.</para>
    /// </summary>
    public const string BufferLevelSignal = "Level";

    private readonly GatewayOptions _options;
    private readonly SignalPolicy _policy;
    private readonly ILogger<Subscriptions> _logger;
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
        GatewayOptions options, SignalPolicy policy, ILogger<Subscriptions> logger,
        Func<IngestRecord, Task> onRecord)
    {
        _options = options;
        _policy = policy;
        _logger = logger;
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
        var skipped = new List<string>();
        foreach (var station in space.Stations)
        {
            foreach (var signal in station.Signals)
            {
                AddDataItem(
                    items, skipped, subscription,
                    new StreamKey(StreamOwner.Station, station.Code, signal.Name),
                    signal.NodeId, _policy.For(signal.Name, signal.Type));
            }

            if (station.EmitsEvents)
            {
                // One item, one filter, for whatever types this station declares — the
                // select clauses and the decoder are the same object, so what was asked for
                // and what is read back cannot drift apart.
                var spec = EventStreamSpec.For(station);
                var events = new MonitoredItem(subscription.DefaultItem)
                {
                    StartNodeId = station.NodeId,
                    AttributeId = Attributes.EventNotifier,
                    NodeClass = NodeClass.Object,
                    DisplayName = $"{station.Code}.{EventStream}",
                    Handle = new EventStreamHandle(
                        new StreamKey(StreamOwner.Station, station.Code, EventStream), spec),
                    SamplingInterval = 0,
                    QueueSize = _options.EventQueueSize,
                    Filter = spec.BuildFilter(),
                };
                events.Notification += OnEvent;
                items.Add(events);
            }
        }

        foreach (var buffer in space.Buffers)
        {
            AddDataItem(
                items, skipped, subscription,
                new StreamKey(StreamOwner.Buffer, buffer.Code, BufferLevelSignal),
                buffer.LevelNodeId, _policy.For(BufferLevelSignal, buffer.LevelType));
        }

        // Said out loud, beside the refusals below. This is the one key in the policy whose
        // whole purpose is to drop data on purpose, and a discovered stream that silently
        // stops being stored is indistinguishable from the loss everything else here exists
        // to prevent -- including to whoever mounted the file and to whoever reads /status.
        if (skipped.Count > 0)
        {
            LogSkippedStreams(_logger, skipped.Count, string.Join(", ", skipped));
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

    [LoggerMessage(
        Level = LogLevel.Warning,
        Message = "the signal policy skips {Count} discovered stream(s), which will not be "
            + "stored: {Streams}")]
    private static partial void LogSkippedStreams(ILogger logger, int count, string streams);

    private void AddDataItem(
        List<MonitoredItem> items, List<string> skipped, Subscription subscription,
        StreamKey key, NodeId node, SignalRule rule)
    {
        if (!rule.Subscribe)
        {
            skipped.Add($"{key.Code}.{key.Signal}");
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

        // The decoder travels on the item rather than being looked up by station code: the
        // filter that produced these positions is on the same handle, and a second lookup
        // would be a second chance to decode a page against the wrong one.
        var handle = item.Handle as EventStreamHandle
            ?? throw new InvalidOperationException(
                $"event item '{item.DisplayName}' carries no stream to decode against");

        _ = _onRecord(handle.Spec.Decode(
            handle.Key.Code, item.StartNodeId.ToString(), fields.EventFields));
    }

    /// <summary>What an event monitored item carries: which stream, and how to read it.</summary>
    private sealed record EventStreamHandle(StreamKey Key, EventStreamSpec Spec);
}

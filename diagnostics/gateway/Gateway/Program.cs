using Gateway.Ingest;
using Gateway.Opc;
using Gateway.Status;
using Opc.Ua;

var options = GatewayOptions.FromProcessEnvironment();

if (args.Contains(ConnectTest.Flag, StringComparer.Ordinal))
{
    return await ConnectTest.RunAsync(args, options).ConfigureAwait(false);
}

// Before anything connects or listens. A policy that is missing or malformed is a start-up
// failure on purpose: an operator who mounted the file wrong has to find out at boot, not
// from deadbands that quietly stopped applying weeks later.
var signalPolicy = SignalPolicy.Load(options.SignalPolicyPath);

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
var logger = app.Logger;

var queue = await LocalQueue.OpenAsync(options.QueuePath).ConfigureAwait(false);

QueueDrain? drain = null;
HistoryBackfill? backfill = null;
PostgresWriter? writer = null;
Reconciler? reconciler = null;
if (!string.IsNullOrWhiteSpace(options.PostgresConnectionString))
{
    await PostgresWriter.ApplySchemaAsync(options.PostgresConnectionString).ConfigureAwait(false);
    writer = new PostgresWriter(options.PostgresConnectionString);
    reconciler = new Reconciler(options.PostgresConnectionString);
    drain = new QueueDrain(
        queue,
        writer,
        options,
        app.Services.GetRequiredService<ILoggerFactory>().CreateLogger<QueueDrain>());
}
var connection = new UaConnection(options, DefaultTelemetry.Create(l => l.AddConsole()));

var state = "disconnected";
// Released when a dropped session comes back, so the gap it left gets closed.
using var reconnected = new SemaphoreSlim(0);
DateTime? lastEventSourceTs = null;
DateTime? historyAvailableFrom = null;
var clockAvailable = true;
Subscriptions? subscriptions = null;
connection.StateChanged += next => state = next;
connection.Reconnected += () => reconnected.Release();

// Every field reports what is true rather than a plausible number: before the ingest task
// has built them, there is no backfill to report progress for and no subscription to have
// overflowed, and 0.0 with a state of "connecting" says exactly that.
StatusEndpoint.Map(app, async () => new GatewayStatus(
    State: state,
    LastEventSourceTs: lastEventSourceTs,
    QueueDepth: await queue.DepthAsync().ConfigureAwait(false),
    BackfillProgress: backfill?.Progress ?? 0.0,
    OverflowCount: subscriptions?.OverflowCount ?? 0,
    RowsWritten: drain?.RowsWritten ?? 0,
    HistoryAvailableFrom: historyAvailableFrom?.ToString("O"),
    ClockAvailable: clockAvailable));
StatusEndpoint.MapReconcile(app, reconciler, options.HistoryDepth);

var stopping = app.Lifetime.ApplicationStopping;
var ingest = Task.Run(
    async () =>
    {
        var session = await connection.ConnectAsync(stopping).ConfigureAwait(false);
        var space = await AddressSpace.ResolveAsync(session, stopping).ConfigureAwait(false);

        async Task EnqueueAsync(IngestRecord record)
        {
            if (record.Kind == "event")
            {
                lastEventSourceTs = record.SourceTs;
            }

            await queue.EnqueueAsync(record).ConfigureAwait(false);
        }

        // §4.3's handshake: the plant builds its own history at catch-up speed, so wait for
        // it to finish before reading any of it. The ready signal gates backfill, not the
        // system — the gateway is connected and answering /status throughout.
        if (space.PhaseNodeId is null)
        {
            // Said out loud rather than skipped quietly: without the phase the gateway cannot
            // tell catch-up from live, so it may backfill a history still being written.
            clockAvailable = false;
        }
        else
        {
            while (await connection.ReadPhaseAsync(space.PhaseNodeId, stopping).ConfigureAwait(false)
                   == "catchup")
            {
                state = "waiting_for_history";
                await Task.Delay(options.PhasePollMs, stopping).ConfigureAwait(false);
            }
        }

        // §4.1: stations and buffers are browsed, not configured, and written before anything
        // that references them arrives. A buffer level for a buffer the table does not have
        // is refused deliberately, so this cannot wait until after the subscription starts.
        if (!string.IsNullOrWhiteSpace(options.PostgresConnectionString))
        {
            await TopologyDiscovery
                .UpsertAsync(options.PostgresConnectionString, space.Topology, stopping)
                .ConfigureAwait(false);
        }

        // Drains while backfill runs, or the whole history depth would sit in the local queue
        // waiting for a subscription that has not started yet.
        var draining = drain is null ? Task.CompletedTask : drain.RunAsync(stopping);

        // §4.3: HistoryRead for the past, subscriptions for live, and backfill first — a
        // subscription opened before the plant finishes catching up delivers its history
        // through the live path at ~100 events/second, which is the thing §4.3 exists to
        // avoid. Backfill closes exactly the gap this gateway has.
        var history = new HistoryBackfill(
            HistoryBackfill.Through(() => connection.Session!), space, signalPolicy, EnqueueAsync,
            options,
            app.Services.GetRequiredService<ILoggerFactory>().CreateLogger<HistoryBackfill>());
        backfill = history;

        // One backfill for all three of §4.3's situations — first boot, a downstream outage
        // and an upstream one. They differ in when they run and in nothing else, and two
        // copies of "where does storage end" is two places for the answer to drift.
        async Task BackfillFromStorageAsync()
        {
            var to = DateTime.UtcNow;
            var earliest = to - options.HistoryDepth;
            var stored = writer is null
                ? null
                : await writer.LastStoredSourceTimestampAsync(stopping).ConfigureAwait(false);
            var from = stored ?? earliest;

            // The plant keeps HistoryDepth and no more. An outage longer than that leaves a
            // window HistoryRead cannot return, and asking for it anyway completes clean over
            // data that is simply gone — a success report covering a hole. §4.4: it goes in
            // as a gap marker, and backfill starts where history actually begins.
            if (from < earliest)
            {
                await EnqueueAsync(PostgresWriter.GapRecord(
                    from, earliest, "beyond_history_depth", "session")).ConfigureAwait(false);
                from = earliest;
            }

            historyAvailableFrom = from;
            state = "backfilling";

            // What the ledger already knows this plant publishes. A discovery that comes back
            // short of it is a stream that stopped, and the backfill refuses rather than
            // quietly covering one stream fewer than last time.
            var knownStreams = writer is null
                ? new HashSet<string>(StringComparer.Ordinal)
                : await writer.KnownBackfillStreamsAsync(stopping).ConfigureAwait(false);

            var report = await history.RunAsync(from, to, knownStreams, stopping)
                .ConfigureAwait(false);

            // R1's ledger, one row per stream per window. Written from the backfill's own
            // report rather than recomputed, so the two cannot disagree about what was pulled
            // — and per stream because an aggregate across 25 of them cannot say which one
            // came back short, which is the only thing the ledger is for.
            if (writer is not null)
            {
                foreach (var window in report.Windows)
                {
                    await writer.RecordBackfillWindowAsync(
                        window.From, window.To, window.Stream, window.RowsReturned, window.Pages,
                        (int)window.DurationMs, stopping).ConfigureAwait(false);
                }
            }
        }

        await BackfillFromStorageAsync().ConfigureAwait(false);

        subscriptions = new Subscriptions(
            options,
            signalPolicy,
            app.Services.GetRequiredService<ILoggerFactory>().CreateLogger<Subscriptions>(),
            EnqueueAsync);
        // The returned Subscription is deliberately not held. The session owns it, and the
        // teardown below iterates session.Subscriptions rather than a handle of ours —
        // holding one invited exactly the mistake that comment describes, where a failed
        // transfer leaves a subscription behind that the tracked object does not name.
        await subscriptions.StartAsync(session, space, stopping).ConfigureAwait(false);

        // ConnectAsync reports "live" when the session comes up, which is before backfill has
        // run. Live means subscribed and receiving, so it is claimed here and not earlier.
        state = "live";

        // The other two of §4.3's three situations. Subscriptions are deliberately not
        // transferred across a reconnect (see UaConnection), so nothing resumes by itself:
        // this is what re-subscribes, and what goes and gets whatever the plant produced
        // while it was away, which exists only in its history. Backfill asks storage where
        // it ends, so the window is exactly the outage however long it lasted.
        var supervising = Task.Run(
            async () =>
            {
                while (!stopping.IsCancellationRequested)
                {
                    await reconnected.WaitAsync(stopping).ConfigureAwait(false);
                    await CloseTheGapAsync().ConfigureAwait(false);
                }
            },
            stopping);

        async Task CloseTheGapAsync()
        {
            // Several keep-alive failures can queue several signals for one outage; they all
            // mean the same thing, so the extras are dropped rather than replayed.
            while (reconnected.CurrentCount > 0)
            {
                await reconnected.WaitAsync(0, stopping).ConfigureAwait(false);
            }

            // The subscription comes down first. The SDK transfers it across a reconnect, so a
            // plant that restarted delivers its whole catch-up through the live path at 700x —
            // measured here as 84,000 rows arriving while the phase still read "catchup", which
            // is precisely what §4.3 orders backfill-before-subscribe to prevent. Worse, that
            // flood moves max(source_ts) to now, so the gap-closing backfill then finds nothing
            // to do and the outage is papered over rather than closed.
            // Every subscription on the session, not just the handle this process holds: a
            // failed transfer leaves one behind that belongs to the SDK, and tearing down only
            // the tracked object leaves that one publishing.
            var current = connection.Session;
            if (current is not null)
            {
                foreach (var stale in current.Subscriptions.ToList())
                {
                    await current.RemoveSubscriptionAsync(stale, stopping).ConfigureAwait(false);
                }
            }

            if (space.PhaseNodeId is not null)
            {
                // A plant that restarted rebuilds its history at catch-up speed; reading it
                // mid-catch-up would read a history still being written.
                while (await connection.ReadPhaseAsync(space.PhaseNodeId, stopping)
                           .ConfigureAwait(false) == "catchup")
                {
                    state = "waiting_for_history";
                    await Task.Delay(options.PhasePollMs, stopping).ConfigureAwait(false);
                }
            }

            await BackfillFromStorageAsync().ConfigureAwait(false);

            await subscriptions
                .StartAsync(connection.Session!, space, stopping).ConfigureAwait(false);
            state = "live";
        }

        await Task.WhenAll(draining, supervising).ConfigureAwait(false);
    },
    stopping);

// An unobserved Task exception is silently discarded, which for the one task that does all
// the work would mean a gateway reporting "disconnected" forever with no reason anywhere.
_ = ingest.ContinueWith(
    faulted =>
    {
        // CA1848 wants a LoggerMessage delegate, which needs a partial class that top-level
        // statements cannot host. This fires at most once, on the way down.
#pragma warning disable CA1848
        logger.LogCritical(faulted.Exception, "ingest failed; stopping");
#pragma warning restore CA1848
        app.Lifetime.StopApplication();
    },
    CancellationToken.None,
    TaskContinuationOptions.OnlyOnFaulted,
    TaskScheduler.Default);

await app.RunAsync().ConfigureAwait(false);
await connection.DisposeAsync().ConfigureAwait(false);
await queue.DisposeAsync().ConfigureAwait(false);
return 0;

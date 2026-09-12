using Gateway.Ingest;
using Gateway.Opc;
using Gateway.Status;
using Opc.Ua;

var options = GatewayOptions.FromProcessEnvironment();

if (args.Contains(ConnectTest.Flag, StringComparer.Ordinal))
{
    return await ConnectTest.RunAsync(args, options).ConfigureAwait(false);
}

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
var logger = app.Logger;

var queue = await LocalQueue.OpenAsync(options.QueuePath).ConfigureAwait(false);

QueueDrain? drain = null;
if (!string.IsNullOrWhiteSpace(options.PostgresConnectionString))
{
    await PostgresWriter.ApplySchemaAsync(options.PostgresConnectionString).ConfigureAwait(false);
    drain = new QueueDrain(
        queue,
        new PostgresWriter(options.PostgresConnectionString),
        options,
        app.Services.GetRequiredService<ILoggerFactory>().CreateLogger<QueueDrain>());
}
var connection = new UaConnection(options, DefaultTelemetry.Create(l => l.AddConsole()));

var state = "disconnected";
DateTime? lastEventSourceTs = null;
Subscriptions? subscriptions = null;
connection.StateChanged += next => state = next;

// Backfill arrives in Task 10. Until then BackfillProgress reports what is true — no
// backfill has run — rather than a plausible number.
StatusEndpoint.Map(app, async () => new GatewayStatus(
    State: state,
    LastEventSourceTs: lastEventSourceTs,
    QueueDepth: await queue.DepthAsync().ConfigureAwait(false),
    BackfillProgress: 0.0,
    OverflowCount: subscriptions?.OverflowCount ?? 0,
    RowsWritten: drain?.RowsWritten ?? 0,
    HistoryAvailableFrom: null));

var stopping = app.Lifetime.ApplicationStopping;
var ingest = Task.Run(
    async () =>
    {
        var session = await connection.ConnectAsync(stopping).ConfigureAwait(false);
        var space = await AddressSpace.ResolveAsync(session, stopping).ConfigureAwait(false);

        subscriptions = new Subscriptions(options, async record =>
        {
            if (record.Kind == "event")
            {
                lastEventSourceTs = record.SourceTs;
            }

            await queue.EnqueueAsync(record).ConfigureAwait(false);
        });

        await subscriptions.StartAsync(session, space, stopping).ConfigureAwait(false);

        if (drain is not null)
        {
            await drain.RunAsync(stopping).ConfigureAwait(false);
        }
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

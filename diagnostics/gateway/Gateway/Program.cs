using Gateway.Opc;
using Gateway.Status;

var options = GatewayOptions.FromProcessEnvironment();

if (args.Contains(ConnectTest.Flag, StringComparer.Ordinal))
{
    return await ConnectTest.RunAsync(args, options).ConfigureAwait(false);
}

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// Ingest, backfill and the writer arrive in Tasks 8-10; until then the gateway holds a
// session and reports that truthfully rather than inventing progress it has not made.
var state = "disconnected";
var connection = new UaConnection(options, Opc.Ua.DefaultTelemetry.Create(
    logging => logging.AddConsole()));
connection.StateChanged += next => state = next;

StatusEndpoint.Map(app, () => new GatewayStatus(
    State: state,
    LastEventSourceTs: null,
    QueueDepth: 0,
    BackfillProgress: 0.0,
    OverflowCount: 0,
    RowsWritten: 0,
    HistoryAvailableFrom: null));

app.Lifetime.ApplicationStopping.Register(() => connection.DisposeAsync().AsTask().Wait());

_ = Task.Run(async () =>
{
    try
    {
        await connection.ConnectAsync(app.Lifetime.ApplicationStopping).ConfigureAwait(false);
    }
    catch (OperationCanceledException)
    {
        throw;
    }
});

await app.RunAsync().ConfigureAwait(false);
return 0;

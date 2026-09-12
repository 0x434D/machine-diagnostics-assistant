namespace Gateway.Status;

/// <summary>
/// What the gateway can honestly say about itself. Fields that belong to machinery which
/// does not exist yet report their true value rather than a placeholder: with no local
/// queue there is no depth, and with no writer there are no rows.
/// </summary>
public sealed record GatewayStatus(
    string State,                      // disconnected | connecting | backfilling | live
    DateTime? LastEventSourceTs,       // simulated time, never wall clock (§4.2)
    int QueueDepth,
    double BackfillProgress,           // 0.0 .. 1.0
    int OverflowCount,
    long RowsWritten,
    string? HistoryAvailableFrom);     // "plant booting — history available from 03:14" (§4.3)

public static class StatusEndpoint
{
    public static void Map(WebApplication app, Func<GatewayStatus> snapshot)
    {
        ArgumentNullException.ThrowIfNull(app);
        app.MapGet("/status", () => Results.Json(snapshot()));
    }
}

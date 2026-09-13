namespace Gateway.Ingest;

/// <summary>
/// One thing that crossed the boundary, verbatim, before anything interprets it.
/// </summary>
/// <param name="Kind">"datachange", "event" or "gap".</param>
/// <param name="SourceTs">
/// Simulated time. Every analysis reads this one (§4.2). On a "gap" record it is the start
/// of the window that was lost; the end and the reason are in the payload.
/// </param>
/// <param name="SourceTs">Simulated time. Every analysis reads this one (§4.2).</param>
/// <param name="ServerTs">The plant's wall clock. Diagnostics only.</param>
/// <param name="ImageBytes">Present on rejects only; good parts carry no image (§3.4).</param>
public sealed record IngestRecord(
    string Kind,
    string NodeId,
    DateTime SourceTs,
    DateTime ServerTs,
    uint StatusCode,
    string PayloadJson,
    byte[]? ImageBytes);

using Gateway.Ingest;

namespace Gateway.Tests;

public sealed class LocalQueueTests : IDisposable
{
    private readonly string _path =
        Path.Join(Path.GetTempPath(), $"q-{Guid.NewGuid():N}.db");

    public void Dispose()
    {
        foreach (var suffix in new[] { "", "-wal", "-shm" })
        {
            File.Delete(_path + suffix);
        }
    }

    [Fact]
    public async Task SurvivesProcessRestart()
    {
        // §5.1: the local queue is a SQLite file on a volume — durable, inspectable, and
        // openable mid-outage to prove it is filling.
        await using (var queue = await LocalQueue.OpenAsync(_path))
        {
            await queue.EnqueueAsync(Sample("A-1"));
            await queue.EnqueueAsync(Sample("A-2"));
        }

        await using var reopened = await LocalQueue.OpenAsync(_path);
        Assert.Equal(2, await reopened.DepthAsync());
    }

    [Fact]
    public async Task AckRemovesOnlyAckedRows()
    {
        await using var queue = await LocalQueue.OpenAsync(_path);
        await queue.EnqueueAsync(Sample("A-1"));
        await queue.EnqueueAsync(Sample("A-2"));

        var batch = await queue.DequeueBatchAsync(1);
        await queue.AckAsync(batch.Select(b => b.Id));

        Assert.Equal(1, await queue.DepthAsync());
    }

    [Fact]
    public async Task PreservesImageBytesExactly()
    {
        // R4 measured img_p99 at 110,419 B, so a reject image is well past anything a
        // TEXT column or a careless round-trip would survive.
        var image = new byte[200_000];
        Random.Shared.NextBytes(image);

        await using var queue = await LocalQueue.OpenAsync(_path);
        await queue.EnqueueAsync(Sample("A-1") with { ImageBytes = image });

        var batch = await queue.DequeueBatchAsync(1);
        Assert.Equal(image, batch[0].Record.ImageBytes);
    }

    [Fact]
    public async Task DrainsInInsertionOrder()
    {
        // Insertion order is for draining only. Nothing downstream may assume arrival
        // order (§4.4), which is why SourceTs is carried explicitly and Task 9 orders by it.
        await using var queue = await LocalQueue.OpenAsync(_path);
        await queue.EnqueueAsync(Sample("A-1"));
        await queue.EnqueueAsync(Sample("A-2"));

        var batch = await queue.DequeueBatchAsync(10);

        Assert.Equal(
            [Sample("A-1").PayloadJson, Sample("A-2").PayloadJson],
            batch.Select(b => b.Record.PayloadJson).ToArray());
    }

    [Fact]
    public async Task SourceTimestampSurvivesTheRoundTripAsUtc()
    {
        // SourceTimestamp is simulated time and is what every analysis reads (§4.2).
        // A round-trip that lost the kind would silently shift it by the host's offset.
        await using var queue = await LocalQueue.OpenAsync(_path);
        var sample = Sample("A-1");
        await queue.EnqueueAsync(sample);

        var batch = await queue.DequeueBatchAsync(1);

        Assert.Equal(sample.SourceTs, batch[0].Record.SourceTs);
        Assert.Equal(DateTimeKind.Utc, batch[0].Record.SourceTs.Kind);
    }

    private static IngestRecord Sample(string serial) => new(
        Kind: "event",
        NodeId: "ns=2;i=5",
        SourceTs: new DateTime(2026, 9, 12, 2, 14, 0, DateTimeKind.Utc),
        ServerTs: DateTime.UtcNow,
        StatusCode: 0,
        PayloadJson: $"{{\"AssemblySerial\":\"{serial}\"}}",
        ImageBytes: null);
}

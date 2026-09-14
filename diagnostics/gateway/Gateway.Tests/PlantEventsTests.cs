using Gateway.Opc;
using Opc.Ua;

namespace Gateway.Tests;

/// <summary>
/// §4.1's five event types, written out a third time — the plant states them, the gateway
/// states them, and this file states them again. That is the point: an event notification
/// is a positional EventFieldList, so a field order that quietly moved would re-assign every
/// column with nothing raised, and a test that imported the order it checks would move with
/// it and prove nothing.
/// </summary>
public sealed class PlantEventsTests
{
    private const ushort PlantNamespace = 2;

    private static readonly string[] DefectClasses = ["gap", "crack"];
    private static readonly double[] Confidences = [0.91, 0.04];

    [Fact]
    public void EveryEventTypeCarriesTheFieldsItsStationPublishesInOrder()
    {
        Assert.Equal(
            ["ComponentSerial", "Lane", "LotCode", "Supplier"],
            PlantEvents.ComponentRead.Fields);
        Assert.Equal(
            ["AssemblySerial", "ComponentSerials", "CarrierId"],
            PlantEvents.AssemblyCreated.Fields);
        Assert.Equal(
            ["AssemblySerial", "Curve", "PeakForce", "JoiningDistance"],
            PlantEvents.PartProcessed.Fields);
        Assert.Equal(
            [
                "AssemblySerial", "CarrierId", "Disposition", "DefectClasses", "Confidences",
                "Confidence", "ModelVersion", "Image",
            ],
            PlantEvents.InspectionResult.Fields);
        Assert.Equal(
            ["AssemblySerial", "Disposition", "Reason"],
            PlantEvents.PartCompleted.Fields);
    }

    [Fact]
    public void EveryTypeIsNamedAsThePlantsBrowseNameSpellsIt()
    {
        // The type browse name is what a GeneratesEvent reference resolves to and what the
        // writer routes on. A spelling that drifts from the plant's does not fail to compile;
        // it fails to match, and the stream then ingests into nothing.
        Assert.Equal(
            [
                "ComponentReadEventType", "AssemblyCreatedEventType", "PartProcessedEventType",
                "InspectionResultEventType", "PartCompletedEventType",
            ],
            PlantEvents.All.Select(type => type.TypeName));
    }

    [Fact]
    public void TimeAndTheTypeLeadEverySelectClause()
    {
        // Time is BaseEventType's own and is the event's SourceTimestamp — §5.2's read_at
        // and created_at are that instant, and no type carries a second copy of it. The type
        // reference is what tells two types apart on the one station that emits both.
        var stream = new EventStreamSpec([PlantEvents.ComponentRead, PlantEvents.AssemblyCreated]);

        Assert.Equal(
            [
                "Time", "EventType", "ComponentSerial", "Lane", "LotCode", "Supplier",
                "AssemblySerial", "ComponentSerials", "CarrierId",
            ],
            stream.Fields);
        Assert.Equal(
            stream.Fields,
            stream.BuildFilter().SelectClauses.Select(clause => clause.BrowsePath[0].Name));
    }

    [Fact]
    public void TwoTypesOnOneStreamSharingAFieldNameIsRefused()
    {
        // asyncua historises events per emitting node and de-duplicates the columns by node
        // rather than by name, so two same-named fields on one station produce two identical
        // columns and a CREATE TABLE that fails — swallowed by the historian's own except,
        // leaving that station with no event history and no error. This decoder could not
        // tell the two apart either.
        var clashing = new EventTypeSpec("OtherEventType", ["AssemblySerial"]);

        Assert.Throws<InvalidOperationException>(
            () => new EventStreamSpec([PlantEvents.PartCompleted, clashing]));
    }

    [Fact]
    public void AnEventIsDecodedIntoThePayloadTheWriterRoutesOn()
    {
        // The arrays stay arrays. §5.2 stores the score vector and the curve as arrays, so
        // flattening either here would be the gateway deciding a shape the schema decided.
        var stream = new EventStreamSpec([PlantEvents.InspectionResult]);
        var at = new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc);

        var record = stream.Decode("S3", "ns=2;i=9",
        [
            new Variant(at),
            new Variant(new NodeId("InspectionResultEventType", PlantNamespace)),
            new Variant("A-00000001"),
            new Variant(3u),
            new Variant("reject"),
            new Variant(DefectClasses),
            new Variant(Confidences),
            new Variant(0.87),
            new Variant("sim-1"),
            new Variant(new byte[] { 1, 2, 3 }),
        ]);

        Assert.Equal(at, record.SourceTs);
        Assert.Equal([1, 2, 3], record.ImageBytes);
        Assert.Contains("\"DefectClasses\":[\"gap\",\"crack\"]", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"Confidences\":[0.91,0.04]", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"EventType\":\"InspectionResultEventType\"", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AnEmptyFieldIsAbsentRatherThanAValue()
    {
        // The plant sends "" for a field that does not apply, because the field is not
        // optional on the wire. A good part's reason stored as text becomes a disposition
        // with a nameless cause, and an empty ByteString stored as bytes gives every good
        // part a row in inspection_images — absent and empty are different claims.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        var record = stream.Decode("S4", "ns=2;i=8",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            new Variant(new NodeId("PartCompletedEventType", PlantNamespace)),
            new Variant("A-00000002"),
            new Variant("good"),
            new Variant(string.Empty),
        ]);

        Assert.DoesNotContain("Reason", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AnEventWithNoInstantIsRefusedRatherThanStampedWithTheWallClock()
    {
        // §4.2: SourceTimestamp is simulated time and is what every analysis reads. An event
        // whose own instant did not arrive, stamped "now", lands in the history at a moment
        // that has nothing to do with the part — and nothing downstream can tell it from a
        // real one.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        Assert.Throws<InvalidOperationException>(() => stream.Decode("S4", "ns=2;i=8",
        [
            Variant.Null,
            new Variant(new NodeId("PartCompletedEventType", PlantNamespace)),
            new Variant("A-00000003"),
            new Variant("good"),
            new Variant(string.Empty),
        ]));
    }

    [Fact]
    public void AStreamOfOneTypeNeedsNoDiscriminator()
    {
        // A station that generates exactly one event type can emit no other, so there is
        // nothing here to tell apart and nothing is being guessed. It is also what keeps the
        // three single-type stations ingesting if the server ever answers the EventType
        // clause with nothing.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        var record = stream.Decode("S4", "ns=2;i=8",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            Variant.Null,
            new Variant("A-00000004"),
            new Variant("good"),
            new Variant(string.Empty),
        ]);

        Assert.Contains("\"EventType\":\"PartCompletedEventType\"", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AStreamOfTwoTypesRefusesAnEventItCannotAttributeToOne()
    {
        // S1 emits two. Which type a row is cannot be recovered from which columns happen to
        // be non-null — a good part's reason is empty and an inspection event's image is too
        // — so an unattributable event is refused rather than routed by resemblance.
        var stream = new EventStreamSpec(
            [PlantEvents.ComponentRead, PlantEvents.AssemblyCreated]);

        Assert.Throws<InvalidOperationException>(() => stream.Decode("S1", "ns=2;i=5",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            Variant.Null,
            new Variant("C-1-00000001"),
        ]));
    }
}

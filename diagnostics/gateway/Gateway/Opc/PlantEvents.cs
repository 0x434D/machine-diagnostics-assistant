using System.Globalization;
using System.Text.Json;
using Gateway.Ingest;
using Opc.Ua;

namespace Gateway.Opc;

/// <summary>
/// One plant event type as this gateway decodes it: the browse name of the type, and the
/// fields it carries beyond the ones every event has.
/// </summary>
public sealed record EventTypeSpec(string TypeName, IReadOnlyList<string> Fields);

/// <summary>
/// §4.1's five event types, written out here and deliberately not derived from the plant.
///
/// <para><b>The names are the wire format; the order is this file's own.</b> A notification
/// does arrive as a positional <c>EventFieldList</c> matching the SelectClauses that were
/// asked for — but <see cref="EventStreamSpec.BuildFilter"/> builds those from the same
/// <see cref="EventStreamSpec.Fields"/> list that <see cref="EventStreamSpec.Decode"/> then
/// reads positionally, and the plant assigns by name too. So reordering this list alone
/// re-orders the request and the decode together and mis-assigns nothing.</para>
///
/// <para>What the two sides must agree on is the <b>set of names</b>. A field renamed or
/// dropped on one side is then asked for under a browse path the other does not have, and
/// asyncua answers such a clause with a null Variant and no error, so the column arrives
/// empty for ever with nothing raised. The same silence covers a value published under the
/// wrong key. The list is restated here rather than imported because a check that moves when
/// the thing it checks moves proves nothing — the plant's <c>events.py</c> says the same in
/// the other direction, and a change to either is a change in both.</para>
///
/// <para><b>Two fields lead every stream and belong to no type.</b> <see cref="TimeField"/>
/// is BaseEventType's own, set by the plant to the simulated instant of the cycle, and is
/// the event's SourceTimestamp — §5.2's <c>components.read_at</c> and
/// <c>assemblies.created_at</c> are that instant, and no type carries a second copy of it.
/// <see cref="EventTypeField"/> is the NodeId of the type, which is what tells two types
/// apart on a station that emits both.</para>
///
/// <para><b>Arrays are scalar on the plant's type declarations.</b> asyncua's
/// <c>create_custom_event_type</c> takes (name, VariantType) pairs and has no ValueRank, so
/// <c>ComponentSerials</c>, <c>Curve</c>, <c>DefectClasses</c> and <c>Confidences</c> are
/// declared scalar and carry an array Variant at runtime. The Variant's own array flag is
/// what decodes; the declared rank says nothing and must not be read.</para>
/// </summary>
public static class PlantEvents
{
    /// <summary>BaseEventType's instant, in simulated time. Leads every select clause.</summary>
    public const string TimeField = "Time";

    /// <summary>BaseEventType's own type reference, as a NodeId. The discriminator.</summary>
    public const string EventTypeField = "EventType";

    /// <summary>
    /// The one field that never reaches the payload: it is bytes, it is present on rejects
    /// only, and it goes to its own table (§3.4).
    /// </summary>
    public const string ImageField = "Image";

    /// <summary>One component drawn off one feeder lane, and the lot it came from.</summary>
    public static readonly EventTypeSpec ComponentRead = new(
        "ComponentReadEventType",
        ["ComponentSerial", "Lane", "LotCode", "Supplier"]);

    /// <summary>
    /// The assembly S1 creates when it loads a carrier. A component's index in
    /// <c>ComponentSerials</c> is §5.2's <c>genealogy.position</c>; nothing else orders it.
    /// </summary>
    public static readonly EventTypeSpec AssemblyCreated = new(
        "AssemblyCreatedEventType",
        ["AssemblySerial", "ComponentSerials", "CarrierId"]);

    /// <summary>
    /// §3.4a's press record, against the serial. <c>PeakForce</c> and <c>JoiningDistance</c>
    /// are the same two numbers the historised streams carry; <c>Curve</c> is what neither
    /// of them can reconstruct.
    /// </summary>
    public static readonly EventTypeSpec PartProcessed = new(
        "PartProcessedEventType",
        ["AssemblySerial", "Curve", "PeakForce", "JoiningDistance"]);

    /// <summary>
    /// §3.4's verdict. <c>DefectClasses</c> and <c>Confidences</c> are parallel arrays over
    /// every class the classifier scores, on every event including good parts — the names
    /// ride beside the scores so this gateway needs no private copy of the vocabulary, which
    /// is the copy nothing would keep equal. <c>Confidence</c> is the scalar confidence in
    /// the OK/NOK verdict and is not one of them.
    /// </summary>
    public static readonly EventTypeSpec InspectionResult = new(
        "InspectionResultEventType",
        [
            "AssemblySerial", "CarrierId", "Disposition", "DefectClasses", "Confidences",
            "Confidence", "ModelVersion", ImageField,
        ]);

    /// <summary>§5.2's <c>part_dispositions</c> row: how the part left the line, and why.</summary>
    public static readonly EventTypeSpec PartCompleted = new(
        "PartCompletedEventType",
        ["AssemblySerial", "Disposition", "Reason"]);

    /// <summary>All five, in line order.</summary>
    public static readonly IReadOnlyList<EventTypeSpec> All =
    [
        ComponentRead, AssemblyCreated, PartProcessed, InspectionResult, PartCompleted,
    ];

    /// <summary>
    /// The type this gateway knows under <paramref name="typeName"/>, or null. Null is a
    /// plant publishing an event type this build cannot decode, which every caller here
    /// treats as loud rather than as a stream to skip.
    /// </summary>
    public static EventTypeSpec? ByName(string typeName) =>
        All.FirstOrDefault(type => string.Equals(type.TypeName, typeName, StringComparison.Ordinal));
}

/// <summary>
/// One station's event stream: which types it carries, the select clauses that read them,
/// and the decoder those clauses are the contract for.
///
/// <para>One monitored item and one HistoryRead cover a station's whole event stream —
/// asyncua historises events per emitting node, not per type — so a station emitting two
/// types is read through one filter whose clauses are the union of both. That union is why
/// the type NodeId is selected: which type a row is cannot be recovered from which columns
/// happen to be non-null, and guessing it would put a press record in the genealogy.</para>
/// </summary>
public sealed class EventStreamSpec
{
    private readonly IReadOnlyDictionary<NodeId, string> _typeNames;

    /// <param name="types">The types this station declares, in the order their fields are read.</param>
    /// <param name="typeNames">
    /// The NodeId each declared type was discovered under. Empty is allowed and means the
    /// decoder has only <paramref name="types"/> to go on, which is enough for a station
    /// that declares exactly one.
    /// </param>
    /// <exception cref="InvalidOperationException">
    /// two of the types share a field name. The plant cannot historise that — asyncua
    /// de-duplicates event columns by node rather than by name, and the CREATE TABLE that
    /// fails on the collision is swallowed, leaving the station with no event history at
    /// all — and this decoder could not tell the two apart either.
    /// </exception>
    public EventStreamSpec(
        IReadOnlyList<EventTypeSpec> types,
        IReadOnlyDictionary<NodeId, string>? typeNames = null)
    {
        ArgumentNullException.ThrowIfNull(types);

        Types = types;
        _typeNames = typeNames ?? new Dictionary<NodeId, string>();

        var fields = new List<string> { PlantEvents.TimeField, PlantEvents.EventTypeField };
        foreach (var field in types.SelectMany(type => type.Fields))
        {
            if (fields.Contains(field, StringComparer.Ordinal))
            {
                throw new InvalidOperationException(
                    $"two event types on one stream both carry '{field}'; the plant cannot "
                    + "historise that and this decoder cannot tell them apart");
            }

            fields.Add(field);
        }

        Fields = fields;
    }

    /// <summary>
    /// The stream one discovered station publishes: the types it declares, matched against
    /// §4.1's table above and ordered by it rather than by the order they browsed out in, so
    /// that the select clauses this produces are the same on every run.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the station declares an event type this build cannot decode, or declares none at all.
    /// Both are loud: an event stream read through a filter that does not match it decodes
    /// into nothing while the ledger counts every row of it as pulled, which is a green run
    /// over missing data.
    /// </exception>
    public static EventStreamSpec For(DiscoveredStation station)
    {
        ArgumentNullException.ThrowIfNull(station);

        var declared = station.EventTypes
            .Select(type => type.TypeName)
            .ToHashSet(StringComparer.Ordinal);

        var unknown = declared
            .Where(name => PlantEvents.ByName(name) is null)
            .Order(StringComparer.Ordinal)
            .ToList();
        if (unknown.Count > 0)
        {
            throw new InvalidOperationException(
                $"station {station.Code} generates {string.Join(", ", unknown)}, which this "
                + "gateway has no field order for; its events would be read and decoded into "
                + "nothing");
        }

        var types = PlantEvents.All.Where(type => declared.Contains(type.TypeName)).ToList();
        if (types.Count == 0)
        {
            throw new InvalidOperationException(
                $"station {station.Code} notifies events but declares no event type, so there "
                + "is no field order to read its history with");
        }

        var byId = station.EventTypes.ToDictionary(type => type.NodeId, type => type.TypeName);
        return new EventStreamSpec(types, byId);
    }

    public IReadOnlyList<EventTypeSpec> Types { get; }

    /// <summary>The select-clause order, which is the positional decoding contract.</summary>
    public IReadOnlyList<string> Fields { get; }

    public EventFilter BuildFilter()
    {
        var select = new SimpleAttributeOperandCollection();
        foreach (var field in Fields)
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
    /// One decoder for one event, used by the live subscription and by the history backfill.
    /// Two decoders would be two chances to disagree about the payload shape the writer
    /// depends on.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the event carries no instant, or names a type this stream does not.
    /// </exception>
    public IngestRecord Decode(string station, string nodeId, IList<Variant> fields)
    {
        ArgumentNullException.ThrowIfNull(fields);

        byte[]? image = null;
        string? typeName = null;
        DateTime? sourceTs = null;
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["Station"] = station,
        };

        for (var i = 0; i < Fields.Count && i < fields.Count; i++)
        {
            var name = Fields[i];
            var value = fields[i].Value;

            if (name == PlantEvents.TimeField)
            {
                sourceTs = value as DateTime?;
                continue;
            }

            if (name == PlantEvents.EventTypeField)
            {
                typeName = TypeNameOf(value as NodeId);
                continue;
            }

            if (name == PlantEvents.ImageField)
            {
                // Rejects only; a good part carries no image and that is not a data gap
                // (§3.4). The server sends an empty ByteString rather than a null one for a
                // good part, and an empty byte[] is not "no image" — stored as-is it would
                // put a row in inspection_images for every good part.
                image = value as byte[] is { Length: > 0 } bytes ? bytes : null;
                continue;
            }

            // Absent and empty are different claims, and the plant sends "" for a field that
            // does not apply — an empty Reason on a good part, say. Stored as text it becomes
            // a disposition reason whose name is empty, and every breakdown then reports good
            // parts as having had a cause.
            if (Encode(value) is { } encoded)
            {
                payload[name] = encoded;
            }
        }

        payload[PlantEvents.EventTypeField] = typeName
            ?? throw new InvalidOperationException(
                $"an event from {station} names a type this gateway does not decode; it "
                + $"carries {string.Join(", ", Types.Select(type => type.TypeName))}");

        return new IngestRecord(
            Kind: "event",
            NodeId: nodeId,
            // Never the wall clock. §4.2: every analysis reads SourceTimestamp, so an event
            // stamped "now" because its own instant was missing is a row placed in simulated
            // time by a number that has nothing to do with it.
            SourceTs: sourceTs ?? throw new InvalidOperationException(
                $"an event from {station} carries no {PlantEvents.TimeField}, so it cannot be "
                + "placed in simulated time"),
            ServerTs: DateTime.UtcNow,
            StatusCode: 0,
            PayloadJson: JsonSerializer.Serialize(payload),
            ImageBytes: image);
    }

    private string? TypeNameOf(NodeId? id)
    {
        if (id is not null && _typeNames.TryGetValue(id, out var discovered))
        {
            return discovered;
        }

        // A stream that declares exactly one type can carry no other, so there is nothing
        // here to discriminate and nothing is being guessed. It is also what keeps a station
        // ingesting when the server answers the EventType clause with nothing — which for
        // the four single-type stations is a field this gateway would otherwise depend on
        // without ever needing it.
        return Types.Count == 1 ? Types[0].TypeName : null;
    }

    /// <summary>
    /// One event field as JSON. Arrays stay arrays — §5.2 stores the curve and the score
    /// vector as arrays, and flattening them here would be this gateway deciding a shape the
    /// schema has already decided.
    /// </summary>
    private static object? Encode(object? value) => value switch
    {
        null => null,
        string text => text.Length == 0 ? null : text,
        string[] texts => texts,
        double[] numbers => numbers,

        // Everything else §4.1 declares is a number: Double, UInt32. An unforeseen type
        // fails here rather than being flattened to a string the writer would then store as
        // a number that was never sent.
        _ => Convert.ToDouble(value, CultureInfo.InvariantCulture),
    };
}

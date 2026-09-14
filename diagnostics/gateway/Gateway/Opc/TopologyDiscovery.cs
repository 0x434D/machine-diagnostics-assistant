using System.Globalization;
using Npgsql;
using Opc.Ua;

namespace Gateway.Opc;

/// <summary>One historised variable under a station, with the data type the server declares.</summary>
public sealed record DiscoveredSignal(string Name, NodeId NodeId, BuiltInType Type);

/// <summary>
/// One event type a station declares it generates, as browsed. The browse name is what the
/// gateway matches its own table of §4.1's types against; the NodeId is what an event's own
/// <c>EventType</c> field carries, and is how two types on one station are told apart.
/// </summary>
public sealed record DiscoveredEventType(string TypeName, NodeId NodeId);

public sealed record DiscoveredStation(
    string Code,
    string Name,
    NodeId NodeId,
    IReadOnlyList<DiscoveredSignal> Signals,
    bool EmitsEvents,
    IReadOnlyList<DiscoveredEventType> EventTypes);

/// <summary>
/// A buffer between two stations. <paramref name="Upstream"/> and <paramref name="Downstream"/>
/// are station <i>codes</i>: the plant publishes browse names ("S2_Joining") and §5.2's
/// stations table is keyed on the code ("S2"), so the conversion happens here, once, at the
/// boundary that reads them. Two codes for one station means two rows for one station.
/// </summary>
public sealed record DiscoveredBuffer(
    string Code,
    NodeId LevelNodeId,
    BuiltInType LevelType,
    string Upstream,
    string Downstream,
    int Capacity);

public sealed record DiscoveredTopology(
    IReadOnlyList<DiscoveredStation> Stations, IReadOnlyList<DiscoveredBuffer> Buffers);

/// <summary>
/// §4.1: the line's topology is discovered, not configured. Nothing here hardcodes which
/// stations exist, which signals they carry or that S2 follows S1 — M2a's four stations and
/// three buffers are browsed the same way a real plant's would be.
/// </summary>
public static class TopologyDiscovery
{
    private const string CapacityVariable = "Capacity";
    private const string UpstreamVariable = "UpstreamStation";
    private const string DownstreamVariable = "DownstreamStation";

    public static async Task<DiscoveredTopology> DiscoverAsync(
        ISession session, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);

        var stations = await DiscoverStationsAsync(session, ct).ConfigureAwait(false);
        var buffers = await DiscoverBuffersAsync(session, ct).ConfigureAwait(false);
        return new DiscoveredTopology(stations, buffers);
    }

    /// <summary>
    /// "S3_Inspection" is the browse name; §5.2's stations table wants the code and the name
    /// apart, and §3.1 names the stations S1..S4 with the function beside them. Keeping the
    /// whole browse name as the code would also disagree with what the ingest path writes,
    /// and two codes for one station means two rows for one station.
    /// </summary>
    public static (string Code, string Name) SplitBrowseName(string browseName)
    {
        ArgumentNullException.ThrowIfNull(browseName);
        var separator = browseName.IndexOf('_', StringComparison.Ordinal);
        return separator < 0
            ? (browseName, browseName)
            : (browseName[..separator], browseName[(separator + 1)..]);
    }

    /// <summary>
    /// Line order, derived from the buffers rather than from the order the stations browsed
    /// out in: the station that is no buffer's downstream is first, and each buffer's
    /// downstream is one past its upstream.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// the buffers do not form one chain over every station — they branch, they skip a
    /// station, or they name one that was not discovered. §5.2's position_in_line orders
    /// every propagation query, so an arbitrary order here is a wrong answer everywhere
    /// later, with nothing to distinguish it from a right one.
    /// </exception>
    public static IReadOnlyList<string> OrderStations(
        IReadOnlyList<string> stationCodes, IReadOnlyList<DiscoveredBuffer> buffers)
    {
        ArgumentNullException.ThrowIfNull(stationCodes);
        ArgumentNullException.ThrowIfNull(buffers);

        var known = new HashSet<string>(stationCodes, StringComparer.Ordinal);
        if (known.Count != stationCodes.Count)
        {
            throw new InvalidOperationException(
                "two stations browsed out under the same code; the line cannot be ordered");
        }

        var downstreamOf = new Dictionary<string, string>(StringComparer.Ordinal);
        var hasUpstream = new HashSet<string>(StringComparer.Ordinal);
        foreach (var buffer in buffers)
        {
            if (!known.Contains(buffer.Upstream) || !known.Contains(buffer.Downstream))
            {
                throw new InvalidOperationException(
                    $"buffer {buffer.Code} sits between {buffer.Upstream} and {buffer.Downstream}, "
                    + "and at least one of those stations was not discovered");
            }

            if (!downstreamOf.TryAdd(buffer.Upstream, buffer.Downstream))
            {
                throw new InvalidOperationException(
                    $"{buffer.Upstream} feeds two buffers; the line branches and has no single order");
            }

            if (!hasUpstream.Add(buffer.Downstream))
            {
                throw new InvalidOperationException(
                    $"{buffer.Downstream} is fed by two buffers; the line merges and has no single order");
            }
        }

        var first = stationCodes.Where(code => !hasUpstream.Contains(code)).ToList();
        if (first.Count != 1)
        {
            throw new InvalidOperationException(
                $"{first.Count} stations are no buffer's downstream; a line has exactly one first "
                + $"station (found: {string.Join(", ", first)})");
        }

        // Terminates: every station is at most one buffer's downstream and the head is none's,
        // so no cycle is reachable from it. A cycle elsewhere leaves the chain short, below.
        var order = new List<string>();
        for (var code = first[0]; ;)
        {
            order.Add(code);
            if (!downstreamOf.TryGetValue(code, out var next))
            {
                break;
            }

            code = next;
        }

        return order.Count == stationCodes.Count
            ? order
            : throw new InvalidOperationException(
                $"the buffers chain {order.Count} of {stationCodes.Count} stations "
                + $"({string.Join(" -> ", order)}); the rest of the line is unreachable");
    }

    /// <summary>
    /// Writes the discovered topology. Idempotent: re-running after a reconnect updates what
    /// changed and adds no rows. One transaction, because a half-written topology is a line
    /// whose buffers reference stations that are not there.
    /// </summary>
    public static async Task UpsertAsync(
        string connectionString, DiscoveredTopology topology, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(topology);

        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);
        await using var transaction =
            await connection.BeginTransactionAsync(ct).ConfigureAwait(false);

        foreach (var station in topology.Stations)
        {
            // INSERT ... SELECT ... WHERE NOT EXISTS, not ON CONFLICT. Both DO UPDATE and
            // DO NOTHING evaluate the id column's nextval before the conflict is detected, so
            // either form advances the SMALLSERIAL sequence once per station per discovery —
            // measured: one reconnect moved it from 1 to 2 against an unchanged table. This
            // form produces no row at all when the station exists, so nextval never runs.
            await ExecuteAsync(
                connection,
                """
                INSERT INTO stations (code, name)
                SELECT $1, $2 WHERE NOT EXISTS (SELECT 1 FROM stations WHERE code = $1)
                """,
                ct, station.Code, station.Name).ConfigureAwait(false);

            await ExecuteAsync(
                connection,
                "UPDATE stations SET name = $2 WHERE code = $1 AND name IS DISTINCT FROM $2",
                ct, station.Code, station.Name).ConfigureAwait(false);
        }

        // position_in_line was left null in M1 because it is derivable from the buffers, and
        // the buffers arrive in M2a. With no buffers there is nothing to derive it from, and
        // an unknown position is written as null rather than guessed — absence of evidence.
        // Buffers that contradict each other are the other case, and OrderStations throws.
        if (topology.Buffers.Count > 0)
        {
            var order = OrderStations(
                [.. topology.Stations.Select(station => station.Code)], topology.Buffers);

            for (var position = 0; position < order.Count; position++)
            {
                await ExecuteAsync(
                    connection,
                    """
                    UPDATE stations SET position_in_line = $2
                    WHERE code = $1 AND position_in_line IS DISTINCT FROM $2
                    """,
                    ct, order[position], (short)(position + 1)).ConfigureAwait(false);
            }
        }
        else
        {
            // Cleared, not left alone. "Null rather than a guess" is an argument about writing
            // an order nothing supports, and a stale order from a plant that no longer
            // publishes buffers is exactly that — with the extra harm that it looks discovered.
            await ExecuteAsync(
                connection,
                "UPDATE stations SET position_in_line = NULL WHERE position_in_line IS NOT NULL",
                ct).ConfigureAwait(false);
        }

        foreach (var buffer in topology.Buffers)
        {
            // Resolved here rather than as a subselect inside the INSERT: a subselect that
            // matches nothing inserts nothing and reports success, and the first buffer level
            // to arrive then fails against a buffers table that quietly has no row for it.
            var upstream = await StationIdAsync(connection, buffer, buffer.Upstream, ct)
                .ConfigureAwait(false);
            var downstream = await StationIdAsync(connection, buffer, buffer.Downstream, ct)
                .ConfigureAwait(false);

            // Same form and the same reason as stations above: buffers.id is SMALLSERIAL, and
            // M1 measured 55,956 discoveries exhausting its range on a table holding one row.
            await ExecuteAsync(
                connection,
                """
                INSERT INTO buffers (code, upstream_station_id, downstream_station_id, capacity)
                SELECT $1, $2, $3, $4 WHERE NOT EXISTS (SELECT 1 FROM buffers WHERE code = $1)
                """,
                ct, buffer.Code, upstream, downstream, (short)buffer.Capacity)
                .ConfigureAwait(false);

            await ExecuteAsync(
                connection,
                """
                UPDATE buffers
                SET upstream_station_id = $2, downstream_station_id = $3, capacity = $4
                WHERE code = $1
                  AND (upstream_station_id, downstream_station_id, capacity)
                      IS DISTINCT FROM ($2, $3, $4)
                """,
                ct, buffer.Code, upstream, downstream, (short)buffer.Capacity)
                .ConfigureAwait(false);
        }

        await transaction.CommitAsync(ct).ConfigureAwait(false);
    }

    /// <summary>
    /// One browsed variable as a stream to ingest, or null where the plant says it keeps no
    /// history of it.
    ///
    /// <para>A node the plant declares live-only is the plant saying not to store it (M2
    /// design D12): three of S1's children restate what an event already carries
    /// authoritatively, and historising them would be the second, weaker copy §3.4a warns
    /// about. Subscribed and backfilled anyway, they are three streams with no history to
    /// read and no row in the plant's own ledger to reconcile against — which shows up
    /// nowhere in <c>make check</c>, because the plant's browse-count test asserts the
    /// plant's tree and stays green whatever this gateway does with it.</para>
    ///
    /// <para><b>Only an explicit false drops a stream.</b> A server that does not answer the
    /// attribute at all has said nothing, and losing a whole stream on silence is the one
    /// outcome §5.1 refuses — the same asymmetry the signal policy's fail-open default
    /// has.</para>
    /// </summary>
    public static DiscoveredSignal? HistorisedSignal(
        string name, NodeId node, DataValue dataType, DataValue historizing) =>
        !StatusCode.IsBad(historizing.StatusCode) && historizing.Value is false
            ? null
            : new DiscoveredSignal(name, node, BuiltInTypeOf(dataType));

    private static async Task<IReadOnlyList<DiscoveredStation>> DiscoverStationsAsync(
        ISession session, CancellationToken ct)
    {
        var folder = await AddressSpace.TranslateAsync(session, ["Line", "Stations"], ct)
            .ConfigureAwait(false);

        var stations = new List<DiscoveredStation>();
        foreach (var child in await BrowseChildrenAsync(session, folder, ct).ConfigureAwait(false))
        {
            var variables = (await BrowseChildrenAsync(session, child.NodeId, ct)
                    .ConfigureAwait(false))
                .Where(node => node.NodeClass == NodeClass.Variable)
                .ToList();

            // Both attributes in one read per variable pair, interleaved, because two reads
            // over the same node set is two round trips and two chances for the results to
            // be paired up by index against different lists.
            var attributes = await ReadAsync(
                session,
                [
                    .. variables.SelectMany(v => new[]
                    {
                        Attribute(v.NodeId, Attributes.DataType),
                        Attribute(v.NodeId, Attributes.Historizing),
                    }),
                ],
                ct).ConfigureAwait(false);

            var signals = new List<DiscoveredSignal>(variables.Count);
            for (var i = 0; i < variables.Count; i++)
            {
                if (HistorisedSignal(
                        variables[i].Name, variables[i].NodeId,
                        attributes[(2 * i) + 0], attributes[(2 * i) + 1]) is { } signal)
                {
                    signals.Add(signal);
                }
            }

            var (code, name) = SplitBrowseName(child.Name);
            stations.Add(new DiscoveredStation(
                Code: code,
                Name: name,
                NodeId: child.NodeId,
                Signals: signals,
                EmitsEvents: await EmitsEventsAsync(session, child.NodeId, ct)
                    .ConfigureAwait(false),
                EventTypes: await EventTypesAsync(session, child.NodeId, ct)
                    .ConfigureAwait(false)));
        }

        return stations;
    }

    /// <summary>
    /// Which event types a station declares it generates, per the server's own
    /// <c>GeneratesEvent</c> references rather than per a table saying S1 emits two.
    ///
    /// <para>This is what makes one filter per station possible: a station's whole event
    /// stream is read through one monitored item — asyncua historises events per emitting
    /// node, not per type — so the select clauses have to cover exactly the types that
    /// station has, and the decoder has to be able to tell them apart afterwards.</para>
    /// </summary>
    private static async Task<IReadOnlyList<DiscoveredEventType>> EventTypesAsync(
        ISession session, NodeId station, CancellationToken ct)
    {
        // GeneratesEvent is not a hierarchical reference, so this cannot ride on the browse
        // that finds a station's variables.
        var references = await BrowseAsync(
            session, station, ReferenceTypeIds.GeneratesEvent, NodeClass.ObjectType, ct)
            .ConfigureAwait(false);

        return [.. references.Select(node => new DiscoveredEventType(node.Name, node.NodeId))];
    }

    private static async Task<IReadOnlyList<DiscoveredBuffer>> DiscoverBuffersAsync(
        ISession session, CancellationToken ct)
    {
        // A line with no Buffers folder is a line this gateway still ingests from; only its
        // order is then unknown. §4.1's plant always has one.
        var folder = await AddressSpace
            .TryTranslateAsync(session, ["Line", "Buffers"], ct).ConfigureAwait(false);
        if (folder is null)
        {
            return [];
        }

        var buffers = new List<DiscoveredBuffer>();
        foreach (var child in await BrowseChildrenAsync(session, folder, ct).ConfigureAwait(false))
        {
            var children = (await BrowseChildrenAsync(session, child.NodeId, ct)
                .ConfigureAwait(false)).ToDictionary(node => node.Name, StringComparer.Ordinal);

            var level = Required(children, child.Name, Subscriptions.BufferLevelSignal);
            var capacity = Required(children, child.Name, CapacityVariable);
            var upstream = Required(children, child.Name, UpstreamVariable);
            var downstream = Required(children, child.Name, DownstreamVariable);

            var values = await ReadAsync(
                session,
                [
                    Attribute(level, Attributes.DataType),
                    Attribute(capacity, Attributes.Value),
                    Attribute(upstream, Attributes.Value),
                    Attribute(downstream, Attributes.Value),
                ],
                ct).ConfigureAwait(false);

            buffers.Add(new DiscoveredBuffer(
                Code: child.Name,
                LevelNodeId: level,
                LevelType: BuiltInTypeOf(values[0]),
                Upstream: SplitBrowseName(Text(values[2], child.Name, upstream)).Code,
                Downstream: SplitBrowseName(Text(values[3], child.Name, downstream)).Code,
                Capacity: BufferCapacity(values[1], child.Name, capacity)));
        }

        return buffers;
    }

    /// <summary>
    /// Which station the inspection event stream hangs off, per the server rather than per a
    /// hardcoded S3. A bad status is the server saying this object notifies nothing, which is
    /// an answer and not a failure — the same distinction Clock.Phase's absence gets.
    /// </summary>
    private static async Task<bool> EmitsEventsAsync(
        ISession session, NodeId station, CancellationToken ct)
    {
        var read = await ReadAsync(
            session, [Attribute(station, Attributes.EventNotifier)], ct).ConfigureAwait(false);

        return !StatusCode.IsBad(read[0].StatusCode)
            && read[0].Value is byte notifier
            && (notifier & EventNotifiers.SubscribeToEvents) != 0;
    }

    private static NodeId Required(
        IReadOnlyDictionary<string, ChildNode> children, string buffer, string variable) =>
        children.TryGetValue(variable, out var node)
            ? node.NodeId
            : throw new ServiceResultException(
                StatusCodes.BadNotFound, $"buffer {buffer} publishes no {variable}");

    private static string Text(DataValue value, string buffer, NodeId node) =>
        Answered(value, buffer, node) as string
        ?? throw new ServiceResultException(
            StatusCodes.BadTypeMismatch, $"buffer {buffer}'s {node} is not a string");

    /// <summary>
    /// A buffer's capacity, from the value read off its Capacity node.
    ///
    /// <para>Public because it is the one conversion in discovery whose failure is a plausible
    /// number rather than an exception: <c>Convert.ToInt32(null)</c> is 0, and
    /// <c>buffers.capacity</c> is <c>SMALLINT NOT NULL</c>, so a read that did not answer used
    /// to store a buffer that holds nothing — which is what §3.3's propagation is measured
    /// against. Browsing the node proves it exists and nothing about the read.</para>
    /// </summary>
    /// <exception cref="ServiceResultException">
    /// the read did not answer, or answered with a number that is not a buffer size.
    /// </exception>
    public static int BufferCapacity(DataValue value, string buffer, NodeId node)
    {
        var capacity = Convert.ToInt32(
            Answered(value, buffer, node), CultureInfo.InvariantCulture);

        // short, not int: the column is SMALLINT, and the cast that writes it would otherwise
        // wrap a large capacity into a negative one just as quietly.
        return capacity is >= 0 and <= short.MaxValue
            ? capacity
            : throw new ServiceResultException(
                StatusCodes.BadOutOfRange,
                $"buffer {buffer}'s capacity ({node}) read as {capacity}, which no buffer holds");
    }

    private static object Answered(DataValue value, string buffer, NodeId node) =>
        !StatusCode.IsBad(value.StatusCode) && value.Value is { } answer
            ? answer
            : throw new ServiceResultException(
                StatusCode.IsBad(value.StatusCode) ? value.StatusCode.Code : StatusCodes.BadNoData,
                $"buffer {buffer}'s {node} read as {value.StatusCode} with no value");

    private static BuiltInType BuiltInTypeOf(DataValue value) =>
        value.Value is NodeId dataType && !StatusCode.IsBad(value.StatusCode)
            // Fully qualified: inside Gateway.Opc, "Opc.Ua" would bind to this namespace.
            ? global::Opc.Ua.TypeInfo.GetBuiltInType(dataType)

            // Not a failure: a type this client cannot name is a signal with no deadband, which
            // is what the policy gives an unknown signal anyway.
            : BuiltInType.Null;

    private static ReadValueId Attribute(NodeId node, uint attribute) =>
        new() { NodeId = node, AttributeId = attribute };

    private static async Task<DataValueCollection> ReadAsync(
        ISession session, ReadValueIdCollection nodesToRead, CancellationToken ct)
    {
        if (nodesToRead.Count == 0)
        {
            return [];
        }

        var response = await session
            .ReadAsync(null, 0, TimestampsToReturn.Neither, nodesToRead, ct)
            .ConfigureAwait(false);

        // A short result set is a server that did not answer the whole request. Left unchecked
        // it surfaces several lines later as an IndexOutOfRangeException naming nothing, and
        // the node that went unanswered is exactly what the operator needs told.
        return response.Results.Count == nodesToRead.Count
            ? response.Results
            : throw new ServiceResultException(
                StatusCodes.BadUnexpectedError,
                $"read of {nodesToRead.Count} attributes returned {response.Results.Count} "
                + $"results: {string.Join(", ", nodesToRead.Select(read => read.NodeId))}");
    }

    private sealed record ChildNode(string Name, NodeId NodeId, NodeClass NodeClass);

    private static Task<IReadOnlyList<ChildNode>> BrowseChildrenAsync(
        ISession session, NodeId parent, CancellationToken ct) =>
        BrowseAsync(
            session, parent, ReferenceTypeIds.HierarchicalReferences,
            NodeClass.Object | NodeClass.Variable, ct);

    private static async Task<IReadOnlyList<ChildNode>> BrowseAsync(
        ISession session, NodeId parent, NodeId referenceType, NodeClass nodeClasses,
        CancellationToken ct)
    {
        var response = await session.BrowseAsync(
            null,
            null,
            0,
            [
                new BrowseDescription
                {
                    NodeId = parent,
                    BrowseDirection = BrowseDirection.Forward,
                    ReferenceTypeId = referenceType,
                    IncludeSubtypes = true,
                    NodeClassMask = (uint)nodeClasses,
                    ResultMask = (uint)BrowseResultMask.All,
                },
            ],
            ct).ConfigureAwait(false);

        // Browse order, not dictionary order: it is the order the plant declares its stations
        // in, which is what anyone holding UaExpert sees. Line order comes from the buffers.
        return
        [
            .. response.Results[0].References.Select(reference => new ChildNode(
                reference.BrowseName.Name,
                ExpandedNodeId.ToNodeId(reference.NodeId, session.NamespaceUris),
                reference.NodeClass)),
        ];
    }

    private static async Task<short> StationIdAsync(
        NpgsqlConnection connection, DiscoveredBuffer buffer, string code, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(
            "SELECT id FROM stations WHERE code = $1", connection);
        command.Parameters.AddWithValue(code);
        var id = await command.ExecuteScalarAsync(ct).ConfigureAwait(false);
        return id is short resolved
            ? resolved
            : throw new InvalidOperationException(
                $"buffer {buffer.Code} names station {code}, which the line does not have");
    }

    private static async Task ExecuteAsync(
        NpgsqlConnection connection, string sql, CancellationToken ct, params object[] parameters)
    {
        await using var command = new NpgsqlCommand(sql, connection);
        foreach (var parameter in parameters)
        {
            command.Parameters.AddWithValue(parameter);
        }

        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }
}

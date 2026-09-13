using Npgsql;
using Opc.Ua;

namespace Gateway.Opc;

public sealed record DiscoveredStation(
    string Code, string Name, NodeId NodeId, NodeId? TaktNodeId, NodeId? PartCountNodeId);

/// <summary>
/// §4.1: the line's topology is discovered, not configured. Nothing downstream hardcodes
/// which stations exist or that S2 follows S1, so M2 adds three more stations with no change
/// here — and pointing this at a real plant works the same way, because a real plant's
/// topology also comes from its address space.
/// </summary>
public static class TopologyDiscovery
{
    public static async Task<IReadOnlyList<DiscoveredStation>> DiscoverAsync(
        ISession session, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);

        var stationsFolder = await AddressSpace
            .TranslateAsync(session, ["Line", "Stations"], ct).ConfigureAwait(false);

        var discovered = new List<DiscoveredStation>();
        foreach (var (browseName, nodeId) in await BrowseChildrenAsync(session, stationsFolder, ct)
            .ConfigureAwait(false))
        {
            var children = await BrowseChildrenAsync(session, nodeId, ct).ConfigureAwait(false);
            var (code, name) = SplitBrowseName(browseName);

            discovered.Add(new DiscoveredStation(
                Code: code,
                Name: name,
                NodeId: nodeId,
                TaktNodeId: children.GetValueOrDefault("TaktTime"),
                PartCountNodeId: children.GetValueOrDefault("PartCount")));
        }

        return discovered;
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

    /// <summary>Idempotent: re-running after a reconnect updates names and adds no rows.</summary>
    public static async Task UpsertAsync(
        string connectionString, IReadOnlyList<DiscoveredStation> stations, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(stations);

        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        foreach (var station in stations)
        {
            // INSERT ... SELECT ... WHERE NOT EXISTS, not ON CONFLICT. Both DO UPDATE and
            // DO NOTHING evaluate the id column's nextval before the conflict is detected, so
            // either form advances the SMALLSERIAL sequence once per station per discovery —
            // measured: one reconnect moved it from 1 to 2 against an unchanged table. This
            // form produces no row at all when the station exists, so nextval never runs.
            await using var insert = new NpgsqlCommand(
                """
                INSERT INTO stations (code, name)
                SELECT $1, $2 WHERE NOT EXISTS (SELECT 1 FROM stations WHERE code = $1)
                """,
                connection);
            insert.Parameters.AddWithValue(station.Code);
            insert.Parameters.AddWithValue(station.Name);
            await insert.ExecuteNonQueryAsync(ct).ConfigureAwait(false);

            await using var update = new NpgsqlCommand(
                "UPDATE stations SET name = $2 WHERE code = $1 AND name IS DISTINCT FROM $2",
                connection);
            update.Parameters.AddWithValue(station.Code);
            update.Parameters.AddWithValue(station.Name);
            await update.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }
    }

    private static async Task<Dictionary<string, NodeId>> BrowseChildrenAsync(
        ISession session, NodeId parent, CancellationToken ct)
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
                    ReferenceTypeId = ReferenceTypeIds.HierarchicalReferences,
                    IncludeSubtypes = true,
                    NodeClassMask = (uint)(NodeClass.Object | NodeClass.Variable),
                    ResultMask = (uint)BrowseResultMask.All,
                },
            ],
            ct).ConfigureAwait(false);

        var children = new Dictionary<string, NodeId>(StringComparer.Ordinal);
        foreach (var reference in response.Results[0].References)
        {
            children[reference.BrowseName.Name] =
                ExpandedNodeId.ToNodeId(reference.NodeId, session.NamespaceUris);
        }

        return children;
    }
}

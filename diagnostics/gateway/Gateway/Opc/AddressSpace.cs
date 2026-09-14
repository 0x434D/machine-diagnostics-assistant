using Opc.Ua;

namespace Gateway.Opc;

/// <summary>
/// The plant nodes this gateway ingests from, browsed rather than configured. §4.1: the
/// line's topology is discovered — nothing here may hardcode that S2 follows S1 or that a
/// station carries TaktTime, and asyncua assigns node identifiers automatically, so they are
/// not predictable anyway.
/// </summary>
public sealed record AddressSpace(
    IReadOnlyList<DiscoveredStation> Stations,
    IReadOnlyList<DiscoveredBuffer> Buffers,
    NodeId? PhaseNodeId)
{
    public const string PlantNamespaceUri = "http://machine-agent/plant";

    public DiscoveredTopology Topology => new(Stations, Buffers);

    public static async Task<AddressSpace> ResolveAsync(ISession session, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);

        var topology = await TopologyDiscovery.DiscoverAsync(session, ct).ConfigureAwait(false);

        return new AddressSpace(
            Stations: topology.Stations,
            Buffers: topology.Buffers,

            // §4.1 exposes the clock deliberately: the gateway can see that the machine runs
            // on its own time, in which phase. Optional, because a plant that does not publish
            // one is a plant this gateway should still ingest from — the alternative is a
            // gateway that refuses to start against anything but this simulator, which is the
            // opposite of "point it at a real plant". Its absence is reported, never assumed.
            PhaseNodeId: await TryTranslateAsync(session, ["Line", "Clock", "Phase"], ct)
                .ConfigureAwait(false));
    }

    /// <summary>Resolves a browse path under Objects, looking the namespace up by URI.</summary>
    internal static async Task<NodeId> TranslateAsync(
        ISession session, string[] browseNames, CancellationToken ct) =>
        await TranslateAsync(session, NamespaceIndex(session), browseNames, ct).ConfigureAwait(false);

    /// <summary>Null where the server publishes no such node, which is a fact about the server.</summary>
    internal static async Task<NodeId?> TryTranslateAsync(
        ISession session, string[] browseNames, CancellationToken ct)
    {
        try
        {
            return await TranslateAsync(session, browseNames, ct).ConfigureAwait(false);
        }
        catch (ServiceResultException e) when (e.StatusCode == StatusCodes.BadNotFound)
        {
            // Specifically "this node is not published", which is a fact about the server
            // rather than a failure. Every other status still propagates.
            return null;
        }
    }

    internal static async Task<NodeId> TranslateAsync(
        ISession session, ushort ns, string[] browseNames, CancellationToken ct)
    {
        var path = new BrowsePath { StartingNode = ObjectIds.ObjectsFolder };
        foreach (var name in browseNames)
        {
            path.RelativePath.Elements.Add(new RelativePathElement
            {
                ReferenceTypeId = ReferenceTypeIds.HierarchicalReferences,
                IsInverse = false,
                IncludeSubtypes = true,
                TargetName = new QualifiedName(name, ns),
            });
        }

        var response = await session
            .TranslateBrowsePathsToNodeIdsAsync(null, [path], ct)
            .ConfigureAwait(false);

        var result = response.Results[0];
        if (StatusCode.IsBad(result.StatusCode) || result.Targets.Count == 0)
        {
            throw new ServiceResultException(
                StatusCodes.BadNotFound,
                $"no node at Objects/{string.Join("/", browseNames)}");
        }

        return ExpandedNodeId.ToNodeId(result.Targets[0].TargetId, session.NamespaceUris);
    }

    private static ushort NamespaceIndex(ISession session)
    {
        var index = session.NamespaceUris.GetIndex(PlantNamespaceUri);
        return index < 0
            ? throw new ServiceResultException(
                StatusCodes.BadNotFound,
                $"the server does not publish namespace {PlantNamespaceUri}")
            : (ushort)index;
    }
}

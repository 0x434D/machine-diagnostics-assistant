using Opc.Ua;

namespace Gateway.Opc;

/// <summary>
/// The plant nodes this gateway subscribes to, resolved by browse path rather than
/// configured. §4.1: the line's topology is discovered — nothing here may hardcode that S2
/// follows S1, and asyncua assigns node identifiers automatically, so they are not
/// predictable anyway. Task 11 widens this to the full topology.
/// </summary>
public sealed record AddressSpace(NodeId S3NodeId, NodeId TaktNodeId, NodeId PartCountNodeId)
{
    /// <summary>
    /// The station code the schema keys on. Browse name "S3_Inspection" carries both the code
    /// and the function; §5.2's stations table wants them apart, and Task 11 fills the rest.
    /// </summary>
    public static string StationCode => S3Path[^1].Split('_')[0];

    public const string PlantNamespaceUri = "http://machine-agent/plant";

    private static readonly string[] S3Path = ["Line", "Stations", "S3_Inspection"];

    public static async Task<AddressSpace> ResolveAsync(ISession session, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(session);

        var index = session.NamespaceUris.GetIndex(PlantNamespaceUri);
        if (index < 0)
        {
            throw new ServiceResultException(
                StatusCodes.BadNotFound,
                $"the server does not publish namespace {PlantNamespaceUri}");
        }

        var ns = (ushort)index;
        return new AddressSpace(
            S3NodeId: await TranslateAsync(session, ns, S3Path, ct).ConfigureAwait(false),
            TaktNodeId: await TranslateAsync(session, ns, [.. S3Path, "TaktTime"], ct)
                .ConfigureAwait(false),
            PartCountNodeId: await TranslateAsync(session, ns, [.. S3Path, "PartCount"], ct)
                .ConfigureAwait(false));
    }

    private static async Task<NodeId> TranslateAsync(
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
}

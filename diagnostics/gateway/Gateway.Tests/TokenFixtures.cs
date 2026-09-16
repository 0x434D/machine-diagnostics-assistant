using System.Text.Json;
using System.Text.Json.Serialization;
using Gateway.Opc;

namespace Gateway.Tests;

internal sealed record TokenFixture(
    string Name,
    bool Accepted,
    string Why,
    string Token,
    string? Subject,
    string? Role,
    DateTimeOffset? ExpiresAt);

internal sealed record FixtureSettings(
    string Audience,
    string Issuer,
    string Algorithm,
    string RoleClaim,
    double ClockSkewSeconds);

internal sealed record FixtureDocument(
    FixtureSettings Settings,
    string PublicKey,
    IReadOnlyList<TokenFixture> Tokens);

/// <summary>
/// `diagnostics/auth/fixtures/tokens.json`, read rather than restated.
///
/// <para>The file itself, not a copy of it: a C# fixture set that merely resembled the
/// Python one would let the two implementations of §10.5 drift while both suites stayed
/// green, which is the single thing this pairing exists to prevent. Copied into the output
/// directory by the csproj, the way `config/signals.json` already is and for the same
/// reason.</para>
/// </summary>
internal static class TokenFixtures
{
    private static readonly JsonSerializerOptions Naming = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = false,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    };

    private static readonly FixtureDocument Document =
        JsonSerializer.Deserialize<FixtureDocument>(
            File.ReadAllText(Path.Join(AppContext.BaseDirectory, "fixtures", "tokens.json")),
            Naming)
        ?? throw new InvalidOperationException("fixtures/tokens.json deserialised to null");

    public static FixtureSettings Settings => Document.Settings;

    public static string PublicKey => Document.PublicKey;

    public static IReadOnlyList<TokenFixture> All => Document.Tokens;

    public static TokenFixture Named(string name) =>
        All.FirstOrDefault(fixture => string.Equals(fixture.Name, name, StringComparison.Ordinal))
        ?? throw new InvalidOperationException($"no fixture named '{name}'");

    /// <summary>
    /// The deployment the fixtures were minted for: this file's key, and every other setting
    /// left at the gateway's own default, so a default that moved fails rather than being
    /// papered over by a fixture that moved with it.
    /// </summary>
    public static GatewayOptions Deployment(
        IReadOnlyDictionary<string, string?>? extra = null)
    {
        var environment = new Dictionary<string, string?>(StringComparer.Ordinal)
        {
            ["AUTH_PUBLIC_KEY"] = PublicKey,
        };

        foreach (var (key, value) in extra ?? new Dictionary<string, string?>(StringComparer.Ordinal))
        {
            environment[key] = value;
        }

        return GatewayOptions.FromEnvironment(environment);
    }
}

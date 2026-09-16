using Gateway.Auth;
using Gateway.Opc;
using Microsoft.Extensions.Logging.Abstractions;

namespace Gateway.Tests;

/// <summary>
/// The C# half of a two-implementation agreement. §2.3 splits this system across two
/// languages, so §10.5's rule is written twice — once in `diagnostics/auth/tokens.py` and
/// once in <see cref="TokenGuard"/>, which cannot import it.
///
/// <para>Two implementations of one rule drift, and they drift quietly: the second one goes
/// on accepting what it always accepted while the first gains a check it never learns. What
/// stops that is not a second careful reading of the rule but a shared set of *bytes* —
/// these twelve tokens and `diagnostics/auth/tests/test_fixture_parity.py` read the same
/// file and must reach the same verdict on every one of them.</para>
///
/// <para>The settings are asserted as well, and that is not ceremony. A gateway that checked
/// a different audience, or tolerated five minutes of clock skew where the Python services
/// tolerate none, would agree on all twelve verdicts below and still not be running the same
/// rule.</para>
/// </summary>
public sealed class TokenFixtureParityTests
{
    public static TheoryData<string> FixtureNames
    {
        get
        {
            var names = new TheoryData<string>();
            foreach (var fixture in TokenFixtures.All)
            {
                names.Add(fixture.Name);
            }

            return names;
        }
    }

    private static TokenGuard Guard(GatewayOptions? options = null) =>
        new(options ?? TokenFixtures.Deployment(), TimeProvider.System,
            NullLogger<TokenGuard>.Instance);

    [Fact]
    public void TheFixturesWereMintedForThisDeployment()
    {
        var deployment = GatewayOptions.Default();
        var minted = TokenFixtures.Settings;

        Assert.Equal(deployment.TokenAudience, minted.Audience);
        Assert.Equal(deployment.TokenIssuer, minted.Issuer);
        Assert.Equal(deployment.TokenAlgorithm, minted.Algorithm);
        Assert.Equal(deployment.TokenRoleClaim, minted.RoleClaim);
        Assert.Equal(deployment.TokenClockSkew.TotalSeconds, minted.ClockSkewSeconds);
    }

    [Fact]
    public void EveryCaseThePlanNamesIsPresent()
    {
        // The fixture file is the shared half of the agreement, so a case silently dropped
        // from it weakens both suites at once and neither of them would fail.
        var present = TokenFixtures.All.Select(fixture => fixture.Name).ToHashSet(
            StringComparer.Ordinal);

        Assert.Subset(
            present,
            new HashSet<string>(StringComparer.Ordinal)
            {
                "valid_user", "expired", "wrong_audience", "untrusted_key", "no_role_claim",
                "role_outside_the_two",
            });
    }

    [Theory]
    [MemberData(nameof(FixtureNames))]
    public async Task TheVerdictMatchesTheFixture(string name)
    {
        var fixture = TokenFixtures.Named(name);

        var principal = await Guard().VerifyAsync(fixture.Token).ConfigureAwait(true);

        if (fixture.Accepted)
        {
            Assert.NotNull(principal);
            Assert.Equal(fixture.Subject, principal.Subject);
            Assert.Equal(fixture.Role, principal.Role);
        }
        else
        {
            Assert.Null(principal);
        }
    }

    [Fact]
    public async Task WithNoPublicKeyConfiguredEveryTokenIsRefused()
    {
        // The resting state of a deployment nobody configured, and it fails closed. Any other
        // default makes §14's claim false wherever the setting was forgotten.
        var unconfigured = GatewayOptions.Default();

        foreach (var fixture in TokenFixtures.All)
        {
            Assert.Null(await Guard(unconfigured).VerifyAsync(fixture.Token)
                .ConfigureAwait(true));
        }
    }
}

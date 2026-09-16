using System.Globalization;
using Gateway.Auth;
using Gateway.Opc;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging.Abstractions;

namespace Gateway.Tests;

/// <summary>
/// §14: *"unauthenticated requests to every diagnostics endpoint return 401"*. `/status`
/// reports connection state, queue depth, backfill progress, overflow count and rows
/// written — an operational picture of the boundary, and not public.
///
/// <para>The guard stands in front of the whole application rather than on that one route.
/// §1.8's Python proof enumerates the routes off the app because a hand-written list passes
/// while an endpoint added tomorrow is open by default; a gateway with two endpoints has the
/// same problem and a simpler answer, which is to leave no route to enumerate.</para>
/// </summary>
public sealed class StatusAuthorisationTests
{
    /// <summary>A clock that does not move, so an expiry can be stood either side of.</summary>
    private sealed class StoppedClock(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    /// <summary>
    /// One second after the `expired` fixture's `exp`. Read off the fixture file rather than
    /// written here: a file regenerated with a different instant would otherwise leave these
    /// two tests asserting the skew against nothing.
    /// </summary>
    private static DateTimeOffset OneSecondPastExpiry =>
        (TokenFixtures.Named("expired").ExpiresAt
         ?? throw new InvalidOperationException(
             "the `expired` fixture no longer states its expiry"))
        .AddSeconds(1);

    private static TokenGuard Guard(
        TimeProvider? clock = null, IReadOnlyDictionary<string, string?>? extra = null) =>
        new(TokenFixtures.Deployment(extra), clock ?? TimeProvider.System,
            NullLogger<TokenGuard>.Instance);

    private static async Task<(int Status, bool Reached, string Challenge)> RequestAsync(
        TokenGuard guard, string? authorization)
    {
        var context = new DefaultHttpContext();
        context.Request.Path = "/status";
        if (authorization is not null)
        {
            context.Request.Headers.Authorization = authorization;
        }

        context.Response.Body = new MemoryStream();

        var reached = false;
        var middleware = new RequireToken(
            _ =>
            {
                reached = true;
                return Task.CompletedTask;
            },
            guard);

        await middleware.InvokeAsync(context).ConfigureAwait(true);
        return (context.Response.StatusCode, reached,
            context.Response.Headers.WWWAuthenticate.ToString());
    }

    [Fact]
    public async Task AnUnauthenticatedRequestIsRefused()
    {
        var (status, reached, challenge) = await RequestAsync(Guard(), null)
            .ConfigureAwait(true);

        Assert.Equal(StatusCodes.Status401Unauthorized, status);
        Assert.False(reached, "the endpoint ran for a request that carried no token");
        // RFC 6750. A 401 that does not say how to authenticate is a dead end for a client.
        Assert.Equal("Bearer", challenge);
    }

    [Theory]
    [InlineData("Basic b3BlcmF0b3I6aHVudGVyMg==")]
    [InlineData("Bearer")]
    [InlineData("Bearer ")]
    public async Task ARequestThatCarriesNoBearerTokenIsRefused(string authorization)
    {
        var (status, reached, _) = await RequestAsync(Guard(), authorization)
            .ConfigureAwait(true);

        Assert.Equal(StatusCodes.Status401Unauthorized, status);
        Assert.False(reached);
    }

    [Fact]
    public async Task AnInvalidTokenIsRefusedWithTheSameStatusAsNoTokenAtAll()
    {
        // §10.5's refusals are deliberately indistinguishable from outside: a 401 that told
        // an expired token from a forged one would tell whoever holds it which half to fix.
        foreach (var fixture in TokenFixtures.All.Where(f => !f.Accepted))
        {
            var (status, reached, _) =
                await RequestAsync(Guard(), $"Bearer {fixture.Token}").ConfigureAwait(true);

            Assert.Equal(StatusCodes.Status401Unauthorized, status);
            Assert.False(reached, fixture.Why);
        }
    }

    [Fact]
    public async Task StatusIsReadableByUser()
    {
        // §10.5's matrix puts no gateway action in the admin column. `/status` is an
        // operational view, so a `user` reads it and there is no role check to add here.
        var (status, reached, _) =
            await RequestAsync(Guard(), $"Bearer {TokenFixtures.Named("valid_user").Token}")
                .ConfigureAwait(true);

        Assert.Equal(StatusCodes.Status200OK, status);
        Assert.True(reached);
    }

    [Fact]
    public async Task StatusIsReadableByAdmin()
    {
        var (status, reached, _) =
            await RequestAsync(Guard(), $"Bearer {TokenFixtures.Named("valid_admin").Token}")
                .ConfigureAwait(true);

        Assert.Equal(StatusCodes.Status200OK, status);
        Assert.True(reached);
    }

    [Fact]
    public async Task ATokenOneSecondPastExpiryIsRefusedAtTheDefaultSkewOfZero()
    {
        // Zero, and it matches diagnostics/auth/config.py's default for the reason stated
        // there: every container here runs on one host and reads one kernel clock, so there
        // is no skew to tolerate, and a non-zero default silently extends the life of every
        // token ever issued by that much.
        Assert.Equal(TimeSpan.Zero, GatewayOptions.Default().TokenClockSkew);

        var guard = Guard(new StoppedClock(OneSecondPastExpiry));

        Assert.Null(await guard.VerifyAsync(TokenFixtures.Named("expired").Token)
            .ConfigureAwait(true));
    }

    [Fact]
    public async Task TheSameTokenInsideTheConfiguredSkewIsAccepted()
    {
        var guard = Guard(
            new StoppedClock(OneSecondPastExpiry),
            new Dictionary<string, string?>(StringComparer.Ordinal)
            {
                ["AUTH_CLOCK_SKEW_SECONDS"] = "2",
            });

        var principal = await guard.VerifyAsync(TokenFixtures.Named("expired").Token)
            .ConfigureAwait(true);

        Assert.NotNull(principal);
        Assert.Equal("operator-7", principal.Subject);
    }

    [Theory]
    [InlineData("half a minute")]
    [InlineData("30s")]
    [InlineData("-1")]
    public void AClockSkewThatIsNotANonNegativeNumberOfSecondsIsRefused(string value)
    {
        // §10.3 makes it configuration; a typo silently becoming the default would make an
        // operator's fix invisible, and a negative one expires every token early for reasons
        // nobody would find.
        Assert.Throws<ArgumentException>(() => GatewayOptions.FromEnvironment(
            new Dictionary<string, string?>(StringComparer.Ordinal)
            {
                ["AUTH_CLOCK_SKEW_SECONDS"] = value,
            }));
    }

    [Fact]
    public void TheTokenSettingsComeFromTheSameEnvironmentTheSharedModuleReads()
    {
        // AUTH_, not GATEWAY_. These are the deployment's identity settings and the Python
        // services read exactly these names; a GATEWAY_AUDIENCE that had to be kept equal to
        // AUTH_AUDIENCE by hand is the drift this whole task exists to prevent.
        var options = GatewayOptions.FromEnvironment(
            new Dictionary<string, string?>(StringComparer.Ordinal)
            {
                ["AUTH_PUBLIC_KEY"] = TokenFixtures.PublicKey,
                ["AUTH_AUDIENCE"] = "somewhere-else",
                ["AUTH_ISSUER"] = "https://issuer.test/somewhere-else",
                ["AUTH_ROLE_CLAIM"] = "urn:zitadel:roles",
                ["AUTH_CLOCK_SKEW_SECONDS"] = 90.5.ToString(CultureInfo.InvariantCulture),
            });

        Assert.Equal("somewhere-else", options.TokenAudience);
        Assert.Equal("https://issuer.test/somewhere-else", options.TokenIssuer);
        Assert.Equal("urn:zitadel:roles", options.TokenRoleClaim);
        Assert.Equal(TimeSpan.FromSeconds(90.5), options.TokenClockSkew);
    }
}

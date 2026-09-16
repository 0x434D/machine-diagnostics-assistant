using System.Collections.Frozen;
using System.Security.Cryptography;
using Gateway.Opc;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace Gateway.Auth;

/// <summary>Who is asking, as far as this system is allowed to care.</summary>
/// <param name="Subject">The OIDC <c>sub</c>.</param>
/// <param name="Role">One of <see cref="TokenGuard.Roles"/>.</param>
public sealed record Principal(string Subject, string Role);

/// <summary>
/// §10.5's rule, in C#, because the gateway cannot import the Python module that is the same
/// rule (§2.3). A token is validated against the configured public key, its audience and
/// issuer are checked, and its role claim is read; what comes back is a subject and a role
/// and nothing else, because there is no permission matrix, no group and no user table for
/// anything larger to live in.
///
/// <para><b>Every refusal returns <c>null</c>.</b> The caller answers 401 and says nothing
/// about which check failed — a response that told "expired" from "wrong audience" would
/// tell whoever holds the token which half to fix. The reason goes to the log, where an
/// operator reads it and an attacker does not.</para>
///
/// <para>That this and <c>diagnostics/auth/tokens.py</c> agree is not a matter of both having
/// been written carefully. They are checked against one file of tokens —
/// <c>diagnostics/auth/fixtures/tokens.json</c> — by two suites, which is what keeps a pair
/// of implementations from drifting quietly.</para>
/// </summary>
public sealed class TokenGuard
{
    public const string Admin = "admin";
    public const string User = "user";

    /// <summary>§10.5's two, closed. A third value is not a third role; it is a claim with no
    /// rule behind it.</summary>
    public static readonly FrozenSet<string> Roles =
        new[] { Admin, User }.ToFrozenSet(StringComparer.Ordinal);

    private readonly JsonWebTokenHandler _handler = new()
    {
        // The ClaimTypes.* URI rewriting of the old WS-Federation stack, which would turn
        // `sub` into a nameidentifier claim. Claims are read off the payload below anyway;
        // this is off so that reading them off the identity would agree with it.
        MapInboundClaims = false,
    };

    private readonly TokenValidationParameters? _parameters;
    private readonly string _roleClaim;
    private readonly ILogger<TokenGuard> _log;

    /// <param name="options">§10.3: skew, audience, issuer and key source are configuration.</param>
    /// <param name="clock">
    /// What "now" means when an expiry is checked. Injected rather than read off the wall
    /// clock, so the skew can be stood either side of an expiry in a test without sleeping —
    /// and because a clock reached for directly is the habit §4.2 bans outright.
    /// </param>
    /// <param name="log">Where the reason for a refusal goes, since the response carries none.</param>
    /// <exception cref="ArgumentException">
    /// <c>AUTH_PUBLIC_KEY</c> is set to something that is not a PEM public key. A start-up
    /// failure on purpose: an operator who pasted the key wrong has to find out at boot and
    /// not from every request being refused for a reason the log states once.
    /// </exception>
    public TokenGuard(GatewayOptions options, TimeProvider clock, ILogger<TokenGuard> log)
    {
        ArgumentNullException.ThrowIfNull(options);
        ArgumentNullException.ThrowIfNull(clock);

        _log = log;
        _roleClaim = options.TokenRoleClaim;

        if (options.TokenPublicKey.Length == 0)
        {
            // Fails closed, and stays that way: with no parameters there is nothing to
            // validate against and VerifyAsync refuses everything.
            return;
        }

        var rsa = RSA.Create();
        rsa.ImportFromPem(options.TokenPublicKey);

        var skew = options.TokenClockSkew;
        _parameters = new TokenValidationParameters
        {
            IssuerSigningKey = new RsaSecurityKey(rsa),
            ValidAlgorithms = [options.TokenAlgorithm],
            ValidAudience = options.TokenAudience,
            ValidIssuer = options.TokenIssuer,
            ValidateIssuerSigningKey = true,
            ValidateAudience = true,
            ValidateIssuer = true,
            ValidateLifetime = true,
            RequireSignedTokens = true,
            RequireExpirationTime = true,
            RequireAudience = true,

            // Replaces the built-in lifetime check outright, which is why the expiry is
            // re-required inside it: the built-in one reads the wall clock, and this one asks
            // the injected TimeProvider.
            LifetimeValidator = (notBefore, expires, _, _) =>
            {
                var now = clock.GetUtcNow();
                if (expires is null || now - skew >= new DateTimeOffset(expires.Value))
                {
                    return false;
                }

                return notBefore is null || now + skew >= new DateTimeOffset(notBefore.Value);
            },
        };
    }

    /// <summary>The token's principal, or <c>null</c> and a logged reason.</summary>
    public async Task<Principal?> VerifyAsync(string? token)
    {
        if (string.IsNullOrWhiteSpace(token))
        {
            return Refused("no token presented");
        }

        if (_parameters is null)
        {
            return Refused("no public key is configured, so no token can be trusted");
        }

        // ValidateTokenAsync reports a failed validation in the result rather than by
        // throwing, so there is no exception here to catch and none to swallow: a bad
        // signature is a value this method branches on.
        var result = await _handler.ValidateTokenAsync(token, _parameters).ConfigureAwait(false);
        if (!result.IsValid)
        {
            // The type name, because it is the only place the refusals stay apart.
            return Refused($"{result.Exception.GetType().Name}: {result.Exception.Message}");
        }

        if (result.SecurityToken is not JsonWebToken jwt)
        {
            return Refused($"validated as a {result.SecurityToken.GetType().Name}, not a JWT");
        }

        // `exp`, `aud` and `iss` are required by the parameters above; these three are not
        // required by any library and a claim that is merely absent must not read as a claim
        // that passed. Without this, a token with no `sub` authenticates as nobody and a
        // token with no `iat` is one the issuer never dated.
        if (!jwt.TryGetPayloadValue<object>(JwtRegisteredClaimNames.Iat, out _))
        {
            return Refused("no `iat` claim");
        }

        if (!TryGetString(jwt, JwtRegisteredClaimNames.Sub, out var subject)
            || subject.Length == 0)
        {
            return Refused("the `sub` claim is not a non-empty string");
        }

        if (!jwt.TryGetPayloadValue<object>(_roleClaim, out _))
        {
            return Refused($"no role claim '{_roleClaim}'");
        }

        if (!TryGetString(jwt, _roleClaim, out var role) || !Roles.Contains(role))
        {
            return Refused($"role is not one of {string.Join(", ", Roles.Order(StringComparer.Ordinal))}");
        }

        return new Principal(subject, role);
    }

    /// <summary>
    /// A claim, and only if it is a JSON string.
    ///
    /// <para><c>TryGetPayloadValue&lt;string&gt;</c> on its own converts what it finds — a
    /// number, a boolean, a one-element array — so a <c>role</c> of <c>["admin"]</c> would
    /// come back as the string the Python side refuses. Asking for the raw type first is what
    /// makes the two agree about a claim of the wrong shape.</para>
    /// </summary>
    private static bool TryGetString(JsonWebToken jwt, string claim, out string value)
    {
        value = "";
        if (!jwt.TryGetPayloadValue<object>(claim, out var raw) || raw is not string text)
        {
            return false;
        }

        value = text;
        return true;
    }

    private Principal? Refused(string reason)
    {
        // CA1848 wants a LoggerMessage delegate. This is once per refused request on a
        // service that serves two endpoints, and the source-generated alternative would put
        // the reason somewhere other than beside the check that produced it.
#pragma warning disable CA1848
        _log.LogWarning("token refused: {Reason}", reason);
#pragma warning restore CA1848
        return null;
    }
}

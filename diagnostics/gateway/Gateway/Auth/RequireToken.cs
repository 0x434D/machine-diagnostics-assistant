using Microsoft.AspNetCore.Http;

namespace Gateway.Auth;

/// <summary>
/// Refuse an unauthenticated request before it reaches anything the gateway serves.
///
/// <para>§14: *"unauthenticated requests to every diagnostics endpoint return 401"*.
/// `/status` reports connection state, last event time, queue depth, backfill progress,
/// overflow count and rows written — an operational picture of the boundary between the two
/// stacks, which is not public.</para>
///
/// <para>In front of the whole application rather than on the two routes, and that is the
/// substance of it: §1.8's Python proof enumerates routes off the app because a hand-written
/// list passes while an endpoint added tomorrow is open by default. A gateway with two
/// endpoints has the same problem and a simpler answer — stand at the door and leave no list
/// to keep current. It also answers for the paths this service does *not* serve, since a 404
/// given without a token is still an answer about which paths exist.</para>
///
/// <para><b>There is no 403 here, deliberately.</b> §10.5's matrix puts no gateway action in
/// the admin column: `/status` is an operational view a `user` reads, and a role gate on it
/// would be one this project invented. The distinction between 401 and 403 lives where the
/// admin-only actions do, in the analysis service and the agent.</para>
/// </summary>
public sealed class RequireToken(RequestDelegate next, TokenGuard guard)
{
    /// <summary>RFC 6750. A 401 that does not say how to authenticate is a dead end.</summary>
    private const string Challenge = "Bearer";

    public async Task InvokeAsync(HttpContext context)
    {
        ArgumentNullException.ThrowIfNull(context);

        if (await guard.VerifyAsync(Presented(context.Request)).ConfigureAwait(false) is null)
        {
            // Fully qualified because the csproj aliases the bare `StatusCodes` to the OPC UA
            // type of that name, which this whole service is otherwise about.
            context.Response.StatusCode =
                Microsoft.AspNetCore.Http.StatusCodes.Status401Unauthorized;
            context.Response.Headers.WWWAuthenticate = Challenge;
            // Deliberately uniform and deliberately saying nothing: the guard has already
            // logged which check refused, and a body that repeated it would tell whoever
            // holds the token which half to fix.
            await context.Response
                .WriteAsJsonAsync(new { detail = "authentication required" })
                .ConfigureAwait(false);
            return;
        }

        await next(context).ConfigureAwait(false);
    }

    /// <summary>The bearer token on the request, or null if it carries nothing that is one.</summary>
    private static string? Presented(HttpRequest request)
    {
        var header = request.Headers.Authorization.ToString();
        var space = header.IndexOf(' ', StringComparison.Ordinal);
        if (space < 0
            || !header.AsSpan(0, space).Equals(Challenge, StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        var token = header.AsSpan(space + 1).Trim();
        return token.IsEmpty ? null : token.ToString();
    }
}

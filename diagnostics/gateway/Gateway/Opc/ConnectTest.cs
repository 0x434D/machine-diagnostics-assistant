using Opc.Ua;

namespace Gateway.Opc;

/// <summary>
/// One-shot connect probe: `--connect-test &lt;url&gt; --security &lt;None|Sign&gt;`.
/// measurements/run_r3.py runs this image with exactly those arguments for matrix row 1,
/// reading only the exit code and the combined output, so both are the contract.
/// </summary>
internal static class ConnectTest
{
    public const string Flag = "--connect-test";

    public static async Task<int> RunAsync(string[] args, GatewayOptions defaults)
    {
        var endpointUrl = ValueAfter(args, Flag) ?? defaults.EndpointUrl;
        var securityMode = ValueAfter(args, "--security") ?? defaults.SecurityMode;

        var options = defaults with { EndpointUrl = endpointUrl, SecurityMode = securityMode };
        var telemetry = DefaultTelemetry.Create(logging => logging.AddConsole());

        await using var connection = new UaConnection(options, telemetry);
        using var timeout = new CancellationTokenSource(
            TimeSpan.FromSeconds(options.ConnectTimeoutSeconds));

        try
        {
            await connection.ConnectAsync(timeout.Token).ConfigureAwait(false);
            Console.WriteLine("Good");
            return 0;
        }
        catch (ServiceResultException e)
        {
            // The status code is the diagnosis here: BadTcpEndpointUrlInvalid,
            // BadCertificateUriInvalid and BadCertificateHostNameInvalid are three different
            // mistakes that all present as "it will not connect" (§12).
            Console.Error.WriteLine($"{StatusCodes.GetBrowseName(e.StatusCode)}: {e.Message}");
            return 1;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine(
                $"timed out after {options.ConnectTimeoutSeconds}s dialling {endpointUrl}");
            return 1;
        }
    }

    private static string? ValueAfter(string[] args, string flag)
    {
        var index = Array.IndexOf(args, flag);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }
}

using System.Formats.Asn1;
using System.Security.Cryptography.X509Certificates;

namespace Gateway.Opc;

/// <summary>Reads the fields of an application instance certificate that the handshake turns on.</summary>
public static class CertificateInspector
{
    private const string SubjectAlternativeNameOid = "2.5.29.17";

    // GeneralName ::= CHOICE { ... uniformResourceIdentifier [6] IA5String ... } — RFC 5280 §4.2.1.6.
    // X509SubjectAlternativeNameExtension enumerates DNS names and IP addresses but not URIs,
    // so the one field the UA handshake cares about has to be decoded here.
    private const int UniformResourceIdentifierTag = 6;

    /// <summary>
    /// The certificate's first URI SAN, or null if it carries none. A session is rejected as
    /// BadCertificateUriInvalid unless this matches the configured ApplicationUri exactly.
    /// </summary>
    /// <exception cref="FileNotFoundException">No certificate at <paramref name="certificatePath"/>.</exception>
    public static string? UriSan(string certificatePath)
    {
        using var certificate = X509CertificateLoader.LoadCertificateFromFile(certificatePath);

        var extension = certificate.Extensions
            .FirstOrDefault(candidate => candidate.Oid?.Value == SubjectAlternativeNameOid);
        if (extension is null)
        {
            return null;
        }

        var generalNames = new AsnReader(extension.RawData, AsnEncodingRules.DER).ReadSequence();
        while (generalNames.HasData)
        {
            var tag = generalNames.PeekTag();
            if (tag.TagClass == TagClass.ContextSpecific
                && tag.TagValue == UniformResourceIdentifierTag)
            {
                return generalNames.ReadCharacterString(UniversalTagNumber.IA5String, tag);
            }

            generalNames.ReadEncodedValue();
        }

        return null;
    }
}

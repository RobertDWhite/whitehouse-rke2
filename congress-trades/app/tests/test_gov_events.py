from ingest import form4_xml


def test_parse_form4_xml_builds_structured_title():
    xml = """
    <ownershipDocument>
      <issuer>
        <issuerName>Example Corp</issuerName>
        <issuerTradingSymbol>EXAM</issuerTradingSymbol>
      </issuer>
      <reportingOwner>
        <reportingOwnerId>
          <rptOwnerName>Jane Insider</rptOwnerName>
        </reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-05-20</value></transactionDate>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>12.34</value></transactionPricePerShare>
          </transactionAmounts>
          <postTransactionAmounts>
            <sharesOwnedFollowingTransaction><value>500</value></sharesOwnedFollowingTransaction>
          </postTransactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>
    """

    parsed = form4_xml.parse_form4_xml(xml)

    assert parsed["issuer"] == "EXAM"
    assert parsed["owner"] == "Jane Insider"
    assert parsed["transactions"][0]["security"] == "Common Stock"
    assert parsed["transactions"][0]["code"] == "P"
    assert form4_xml.form4_title("fallback", parsed) == "Form 4: Jane Insider reported 1 transaction in EXAM - codes P"

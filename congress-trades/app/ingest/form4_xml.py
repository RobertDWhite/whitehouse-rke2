import xml.etree.ElementTree as ET


def text(el, path):
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def first_value(el, paths):
    for path in paths:
        value = text(el, path)
        if value:
            return value
    return None


def parse_form4_xml(xml_text):
    root = ET.fromstring(xml_text)
    issuer = first_value(root, ["issuer/issuerTradingSymbol", "issuer/issuerName"])
    owner = text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rows = []
    for path, derivative in (
        ("nonDerivativeTable/nonDerivativeTransaction", False),
        ("derivativeTable/derivativeTransaction", True),
    ):
        for txn in root.findall(path):
            rows.append(
                {
                    "security": text(txn, "securityTitle/value"),
                    "date": text(txn, "transactionDate/value"),
                    "code": text(txn, "transactionCoding/transactionCode"),
                    "shares": first_value(txn, ["transactionAmounts/transactionShares/value", "transactionAmounts/transactionTotalValue/value"]),
                    "price": text(txn, "transactionAmounts/transactionPricePerShare/value"),
                    "owned_after": text(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                    "derivative": derivative,
                }
            )
    return {"issuer": issuer, "owner": owner, "transactions": rows}


def form4_title(fallback, parsed):
    txns = parsed.get("transactions") or []
    if not txns:
        return fallback
    owner = parsed.get("owner") or "insider"
    issuer = parsed.get("issuer") or "issuer"
    codes = ", ".join(sorted({t.get("code") for t in txns if t.get("code")}))
    suffix = f" - codes {codes}" if codes else ""
    return f"Form 4: {owner} reported {len(txns)} transaction{'s' if len(txns) != 1 else ''} in {issuer}{suffix}"

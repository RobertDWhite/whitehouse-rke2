import { partyLetter } from '../api.js'

export default function PartyBadge({ party }) {
  const l = partyLetter(party)
  if (!l) return null
  return <span className={`party party-${l}`} title={party}>{l}</span>
}

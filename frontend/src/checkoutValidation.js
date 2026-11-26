export function deliveryError(details) {
  const name = (details.name || '').trim()
  const phone = (details.phone || '').trim()
  const address = (details.address || '').trim()
  if (!name || name.length > 120) return 'Enter your full name (up to 120 characters).'
  const digits = phone.replace(/\D/g, '')
  if (!/^[0-9+()\-\s]{7,30}$/.test(phone) || digits.length < 7 || digits.length > 15) {
    return 'Enter a valid phone number with 7–15 digits.'
  }
  if (address.length < 12 || address.length > 1000) return 'Enter your full delivery address (12–1,000 characters).'
  return ''
}

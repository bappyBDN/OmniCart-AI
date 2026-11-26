export function productPath(productId) {
  return `/products/${encodeURIComponent(productId)}`
}

export function productIdFromPath(pathname) {
  const match = pathname.match(/^\/products\/([^/]+)\/?$/)
  if (!match) return null
  try { return decodeURIComponent(match[1]) }
  catch { return '__invalid_product_id__' }
}

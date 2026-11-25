export const photoLimitBytes = 15 * 1024 * 1024
export const photoAccept = 'image/jpeg,image/png,image/webp,image/bmp,image/gif,image/tiff,image/avif,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.bmp,.gif,.tif,.tiff,.avif,.heic,.heif'
export const photoFormatLabel = 'JPEG, PNG, WebP, BMP, GIF, TIFF, AVIF or HEIC'
const types = photoAccept.split(',').filter(type => type.startsWith('image/'))

export function acceptedPhoto(file) {
  return file.size <= photoLimitBytes &&
    (types.includes(file.type) || /\.(?:jpe?g|png|webp|bmp|gif|tiff?|avif|heic|heif)$/i.test(file.name))
}

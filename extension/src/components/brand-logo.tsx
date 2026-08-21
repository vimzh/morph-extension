type BrandLogoProps = {
  className?: string
  size?: number
}

export function BrandLogo({ className, size = 28 }: BrandLogoProps) {
  return (
    <img
      src="/morph-logo.svg"
      alt="Morph"
      className={className}
      width={size}
      height={size}
    />
  )
}

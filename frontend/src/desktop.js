export const isDesktop = typeof window !== 'undefined' && !!window.qaDesktop

export const desktop = isDesktop ? window.qaDesktop : null

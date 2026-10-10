export const SUBTABS = [
  ['status', 'Status'],
  ['keys', 'API keys'],
  ['playground', 'Playground'],
  ['docs', 'API docs'],
  ['pricing', 'Pricing'],
] as const

export type Sub = (typeof SUBTABS)[number][0]

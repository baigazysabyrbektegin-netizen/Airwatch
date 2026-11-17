import { describe, expect, it } from 'vitest'
import { breaksFromStats, fillColorExpression, formatNumber, kindBadge, legendBins } from './layers'

const stats = { min: 0, p05: 1, p25: 2, median: 3, p75: 4, p95: 5, max: 9, n_null: 0 }
const ramp = ['a', 'b', 'c', 'd', 'e', 'f']

describe('colour helpers', () => {
  it('builds six legend classes from quantiles', () => {
    const bins = legendBins(stats, ramp)
    expect(bins).toHaveLength(6)
    expect(bins[0]).toMatchObject({ from: 0, to: 1, color: 'a' })
    expect(bins[5]).toMatchObject({ from: 5, to: 9, color: 'f' })
  })

  it('collapses duplicate breaks and refuses degenerate stats', () => {
    expect(breaksFromStats({ ...stats, p05: 2, p25: 2, median: 2, p75: 2, p95: 2 })).toBeNull()
    expect(breaksFromStats(undefined)).toBeNull()
    expect(legendBins(undefined, ramp)).toEqual([])
  })

  it('draws null values transparent, never as the lowest class', () => {
    const expr = fillColorExpression('v', stats, ramp) as unknown[]
    expect(expr[0]).toBe('case')
    expect(expr[1]).toEqual(['==', ['get', 'v'], null])
    expect(expr[2]).toBe('rgba(0,0,0,0)')
  })

  it('draws nothing without class breaks', () => {
    expect(fillColorExpression('v', undefined, ramp)).toBe('rgba(0,0,0,0)')
  })
})

describe('labels', () => {
  it('maps data kinds to badges', () => {
    expect(kindBadge('measured (reference)').tone).toBe('measured')
    expect(kindBadge('MODELLED').tone).toBe('modelled')
    expect(kindBadge('estimated 2019').tone).toBe('estimated')
    expect(kindBadge('static geographic').tone).toBe('static')
  })

  it('never shows missing values as zero', () => {
    expect(formatNumber(null)).toBe('no data')
    expect(formatNumber(undefined)).toBe('no data')
    expect(formatNumber(Number.NaN)).toBe('no data')
    expect(formatNumber(0)).toBe('0')
    expect(formatNumber(12345)).toBe('12,345')
  })
})

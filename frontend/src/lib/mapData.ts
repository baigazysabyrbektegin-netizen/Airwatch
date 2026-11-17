import type { FeatureCollection } from 'geojson'
import type { Station } from './types'

export const BISHKEK_CENTER: [number, number] = [74.6, 42.87]

export const EMPTY_FC: FeatureCollection = { type: 'FeatureCollection', features: [] }

export function stationsToGeoJSON(stations: Station[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: stations
      .filter((s) => s.latitude !== null && s.longitude !== null)
      .map((s) => ({
        type: 'Feature' as const,
        id: s.id,
        geometry: { type: 'Point' as const, coordinates: [s.longitude as number, s.latitude as number] },
        properties: { id: s.id, name: s.name, network: s.network, approximate: s.location_status !== 'confirmed' && s.network !== 'clarity' },
      })),
  }
}


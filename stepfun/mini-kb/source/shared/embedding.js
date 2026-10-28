// One contract for the browser, API validation, and collection schema.
export const EMBEDDING = Object.freeze({
  model: 'Xenova/all-MiniLM-L6-v2',
  revision: '751bff37182d3f1213fa05d7196b954e230abad9',
  dtype: 'q8',
  dimension: 384,
  pooling: 'mean',
  normalize: true,
})
export const BATCH_SIZE = 8

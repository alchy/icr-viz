/// <reference lib="webworker" />

import { analyzeDataset } from '../lib/analysis';
import { projectSamples, computeTensionEdges } from '../lib/dimensionality';

self.onmessage = (e: MessageEvent) => {
  const { id, type, tag, payload } = e.data;

  try {
    let result: any;
    switch (type) {
      case 'analyzeDataset':
        result = analyzeDataset(
          payload.samples, payload.anchors, payload.threshold,
          payload.mode, payload.detection
        );
        break;
      case 'projectSamples':
        result = projectSamples(
          payload.samples, payload.mode, payload.results,
          payload.anchors, payload.config
        );
        break;
      case 'computeTensionEdges':
        result = computeTensionEdges(
          payload.samples, payload.mode, payload.maxEdges
        );
        break;
    }
    self.postMessage({ id, type, tag, result });
  } catch (error: any) {
    self.postMessage({ id, type, tag, error: error.message });
  }
};

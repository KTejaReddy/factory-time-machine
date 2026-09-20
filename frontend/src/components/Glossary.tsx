import type { ReactNode } from "react";

/**
 * Plain-language explanations for the technical terms the app shows.
 * Rendered as a hover/focus tooltip so a beginner never has to google a statistic.
 */
const GLOSSARY: Record<string, { simple: string; technical?: string }> = {
  utilization: {
    simple: "How much of a station's working time is actually used. 90% means it is nearly always busy.",
  },
  queue: {
    simple: "Parts waiting in line for a station to become free.",
  },
  wip: {
    simple: "Work In Process — parts that have started but not finished the line, including everything waiting.",
  },
  bottleneck: {
    simple: "The slowest or busiest station that limits how much the whole factory can produce.",
  },
  throughput: {
    simple: "How many products come out of the factory in a given time.",
  },
  confidence: {
    simple: "How sure the AI is about its answer, from 0% to 100%.",
  },
  "tta": {
    simple: "Test-Time Augmentation — the model looks at the image and its mirror image. If it answers differently, it is not really sure.",
    technical: "TTA: prediction averaged over horizontally flipped inputs; disagreement lowers trust.",
  },
  "grad-cam": {
    simple: "A heatmap showing which parts of the image the AI looked at when deciding. It is not a defect location.",
  },
  "cnn": {
    simple: "A neural network that looks for visual patterns (edges, textures, shapes) to classify an image.",
    technical: "Convolutional Neural Network — stacked convolution + pooling layers feeding a classifier.",
  },
  "pca": {
    simple: "Reduces many related factory measurements into a smaller set of useful patterns.",
    technical: "Principal Component Analysis — eigendecomposition of the covariance matrix of standardised features.",
  },
  "mahalanobis": {
    simple: "A distance measure that asks: how different is this run from normal, in the way normal runs usually vary?",
    technical: "Mahalanobis distance in PCA score space, scaled by component variance.",
  },
  "robust z-score": {
    simple: "How far a value is from the typical value, using medians so one wild number cannot distort the scale.",
    technical: "(x − median) / (1.4826 × MAD).",
  },
  "cohens d": {
    simple: "The size of the difference between two groups, in plain units: 0.2 small, 0.5 medium, 0.8 large.",
  },
  "partial correlation": {
    simple: "The correlation between two things after removing the influence of other measured factors.",
  },
  "discrete-event simulation": {
    simple: "A virtual factory: parts arrive, wait in line, get processed station by station, and the computer tracks every wait.",
  },
  "rate card": {
    simple: "Your own cost numbers (scrap cost, downtime cost per hour…). The datasets contain none, so money figures need yours.",
  },
};

export function Term({ name, children }: { name: keyof typeof GLOSSARY | string; children?: ReactNode }) {
  const entry = GLOSSARY[String(name).toLowerCase()];
  if (!entry) return <>{children ?? name}</>;
  return (
    <span className="term-tip" tabIndex={0}>
      {children ?? String(name)}
      <span className="term-tip-box">
        <strong>{String(name)}</strong>
        <br />
        {entry.technical ? (
          <>
            {entry.simple}
            <em className="mt-1 block text-[10.5px] text-[var(--color-ink-faint)]">{entry.technical}</em>
          </>
        ) : (
          entry.simple
        )}
      </span>
    </span>
  );
}

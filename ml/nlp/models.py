"""Milestone 11 model and validation-only review policy."""

from collections import Counter
import time
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from threadpoolctl import threadpool_limits

from ml.nlp.fmucd import LABEL_ORDER
from ml.pilot.validate_fmucd import normalize_description


SEED = 11021
MODEL_NAMES = ("logistic_regression", "linear_svc")
TOKEN_PATTERN = r"(?u)\b[^\W\d_]\w*(?:['’]\w+)?\b"
MIN_COVERAGE = 0.50
MIN_PER_CLASS = 5
TARGET_SELECTIVE_ACCURACY = 0.90
QUANTILES = (0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


def build_model(name):
    vectorizer = TfidfVectorizer(
        preprocessor=normalize_description,
        lowercase=False,
        token_pattern=TOKEN_PATTERN,
        ngram_range=(1, 2),
        min_df=2,
        sublinear_tf=True,
        norm="l2",
        stop_words=None,
    )
    classifier = {
        "logistic_regression": LogisticRegression(
            solver="lbfgs", C=1.0, max_iter=1000, class_weight="balanced", random_state=SEED
        ),
        "linear_svc": LinearSVC(
            C=1.0, dual="auto", max_iter=5000, class_weight="balanced", random_state=SEED
        ),
    }[name]
    return Pipeline([("tfidf", vectorizer), ("classifier", classifier)])


def fit_model(name, rows):
    model = build_model(name)
    start = time.perf_counter()
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit([row["text"] for row in rows], [row["label"] for row in rows])
    return model, time.perf_counter() - start


def scores(model, texts):
    tfidf, classifier = model["tfidf"], model["classifier"]
    if list(classifier.classes_) != LABEL_ORDER:
        raise ValueError("Unexpected classifier label order")
    features = tfidf.transform(texts)
    decision = classifier.decision_function(features)
    ordered = np.sort(decision, axis=1)
    return {
        "forced": classifier.classes_[np.argmax(decision, axis=1)],
        "margin": ordered[:, -1] - ordered[:, -2],
        "has_features": np.asarray(features.getnnz(axis=1)) > 0,
        "decision": decision,
    }


def metrics(actual, predicted):
    report = classification_report(actual, predicted, labels=LABEL_ORDER, output_dict=True, zero_division=0)
    return {
        "accuracy": float(accuracy_score(actual, predicted)),
        "per_class": {label: report[label] for label in LABEL_ORDER},
        "macro_f1": report["macro avg"]["f1-score"],
        "weighted_f1": report["weighted avg"]["f1-score"],
        "support": len(actual),
        "label_order": LABEL_ORDER,
        "confusion_matrix_actual_rows_predicted_columns": confusion_matrix(actual, predicted, labels=LABEL_ORDER).tolist(),
    }


def accepted(predictions, threshold):
    return predictions["has_features"] & (predictions["margin"] >= threshold)


def selection_stats(actual, predictions, threshold):
    true = np.asarray(actual)
    mask = accepted(predictions, threshold)
    support = Counter(true[mask])
    return {
        "threshold": threshold,
        "coverage": float(mask.mean()),
        "accepted": int(mask.sum()),
        "selective_accuracy": float(np.mean(predictions["forced"][mask] == true[mask])) if mask.any() else None,
        "accepted_by_true_class": {label: support[label] for label in LABEL_ORDER},
    }


def choose_review_rule(train_predictions, actual_validation, validation_predictions):
    margins = train_predictions["margin"][train_predictions["has_features"]]
    thresholds = sorted({0.0, *(float(np.quantile(margins, q)) for q in QUANTILES)})
    trials = [selection_stats(actual_validation, validation_predictions, threshold) for threshold in thresholds]
    admissible = [
        item for item in trials
        if item["coverage"] >= MIN_COVERAGE and min(item["accepted_by_true_class"].values()) >= MIN_PER_CLASS
    ]
    if not admissible:
        raise ValueError("No validation policy has enough coverage to evaluate all three labels")
    meets_target = [item for item in admissible if item["selective_accuracy"] >= TARGET_SELECTIVE_ACCURACY]
    if meets_target:
        chosen = max(meets_target, key=lambda item: (item["coverage"], item["selective_accuracy"], -item["threshold"]))
    else:
        chosen = max(admissible, key=lambda item: (item["selective_accuracy"], item["coverage"], -item["threshold"]))
    return {
        "score": "top_minus_runner_up_native_decision_score",
        "no_features": "manual_review",
        "below_threshold": "manual_review",
        "threshold": chosen["threshold"],
        "validation_target_met": bool(meets_target),
        "target_selective_accuracy": TARGET_SELECTIVE_ACCURACY,
        "minimum_coverage": MIN_COVERAGE,
        "minimum_accepted_per_true_class": MIN_PER_CLASS,
        "threshold_grid": "zero plus quantiles of nonempty training decision margins",
        "training_quantiles": QUANTILES,
        "validation_candidates": trials,
        "selected_validation": chosen,
    }


def evaluate_predictions(actual, predictions, threshold):
    true = np.asarray(actual)
    mask = accepted(predictions, threshold)
    return {
        "forced": metrics(true, predictions["forced"]),
        "review_policy": {
            **selection_stats(true, predictions, threshold),
            "manual_review": int((~mask).sum()),
            "no_vocabulary_features": int((~predictions["has_features"]).sum()),
            "manual_review_by_true_class": {label: int(np.sum((true == label) & ~mask)) for label in LABEL_ORDER},
            "metrics_on_accepted": metrics(true[mask], predictions["forced"][mask]) if mask.any() else None,
        },
    }

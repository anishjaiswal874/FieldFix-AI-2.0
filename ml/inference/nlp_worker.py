"""Load the exact frozen NLP pipeline and return a pilot label over stdio."""

from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import sys

# The artifact refers to the original project's normalizer by module name.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml.inference.worker_io import checked_file, emit, input_bytes, ml_root, watchdog


FREEZE_SHA = "e09bf8920b9ca0fb94c434ebe6c8d05a682de7861779f394f60b462a86ca8a98"
MODEL_SHA = "8c7835a9c065a4a672d9ed8639a962fb5a716a7054d2d84ce4d28731c8eae522"
MODEL_NAME = "linear_svc.joblib"


def load_pipeline():
    run = ml_root() / "runs/nlp/milestone-11-v1"
    freeze = json.loads(checked_file(run / "freeze.json", FREEZE_SHA, 16 * 1024))
    if freeze["file_hashes"][MODEL_NAME] != MODEL_SHA:
        raise ValueError("Unexpected frozen model")
    for package, expected in freeze["software"].items():
        installed = ".".join(map(str, sys.version_info[:3])) if package == "python" else version(package)
        if installed != expected:
            raise ValueError("Incompatible frozen NLP environment")
    model_bytes = checked_file(run / MODEL_NAME, MODEL_SHA, 128 * 1024)
    import joblib
    return joblib.load(BytesIO(model_bytes)), freeze["review_rule"]["threshold"]


def recommend(description: str) -> str | None:
    import numpy as np
    from ml.nlp.models import accepted, scores

    pipeline, threshold = load_pipeline()
    prediction = scores(pipeline, [description])
    if not np.isfinite(prediction["decision"]).all():
        raise ValueError("Invalid classifier output")
    if accepted(prediction, threshold)[0]:
        return str(prediction["forced"][0])
    return None


def inspect_texts(texts, explain):
    import numpy as np
    from ml.nlp.models import accepted, scores

    if not 1 <= len(texts) <= 24 or any(not isinstance(text, str) or len(text.encode("utf-8")) > 4000 for text in texts):
        raise ValueError("Invalid inspection input")
    pipeline, threshold = load_pipeline()
    vectorizer = pipeline["tfidf"]
    predictions = scores(pipeline, texts)
    if not np.isfinite(predictions["decision"]).all():
        raise ValueError("Invalid classifier output")
    eligible = accepted(predictions, threshold)
    items = [{"label": str(label) if passed else None, "margin": float(margin) if passed else None}
             for label, margin, passed in zip(predictions["forced"], predictions["margin"], eligible)]
    result = {"items": items}
    if explain and len(texts) == 1:
        normalized = vectorizer.build_preprocessor()(texts[0])
        tokens = vectorizer.build_tokenizer()(normalized)
        features = vectorizer.transform(texts).getrow(0)
        terms = vectorizer.get_feature_names_out()
        indices = sorted(zip(features.indices, features.data), key=lambda pair: -pair[1])[:10]
        result.update(normalized=normalized, tokens=tokens[:80], token_count=len(tokens),
                      stopwords_removed=bool(vectorizer.get_stop_words()),
                      terms=[{"term": str(terms[index]), "weight": float(weight)} for index, weight in indices])
    return result


def corpus_projection(texts, query):
    import numpy as np
    from sklearn.decomposition import PCA
    from sklearn.metrics.pairwise import cosine_similarity

    if len(texts) > 200 or any(not isinstance(text, str) or len(text.encode("utf-8")) > 4000 for text in texts):
        raise ValueError("Invalid comparison corpus")
    if query is not None and (not isinstance(query, str) or len(query.encode("utf-8")) > 4000):
        raise ValueError("Invalid search text")
    pipeline, _ = load_pipeline()
    features = pipeline["tfidf"].transform(texts)
    scores = cosine_similarity(pipeline["tfidf"].transform([query]), features)[0] if query and texts else []
    points = None
    if query is None and len(texts) >= 2 and features.nnz:
        dense = features.toarray()
        points = (np.zeros((len(texts), 2)) if np.all(dense == dense[0])
                  else PCA(n_components=2, svd_solver="randomized", random_state=11021).fit_transform(dense))
    return {"scores": [float(value) for value in scores], "points": points.tolist() if points is not None else None}


def main() -> int:
    timer = watchdog(13)
    try:
        if sys.argv[1:] == ["--corpus"]:
            payload = json.loads(input_bytes(1000000))
            emit(corpus_projection(payload["texts"], payload["query"]))
        elif sys.argv[1:] == ["--inspect"]:
            payload = json.loads(input_bytes(600000))
            emit(inspect_texts(payload["texts"], payload["explain"]))
        else:
            emit({"label": recommend(input_bytes(4000).decode("utf-8"))})
        return 0
    except Exception:
        # stdout is the private response channel. Failures contain no input or paths.
        return 1
    finally:
        timer.cancel()


if __name__ == "__main__":
    raise SystemExit(main())

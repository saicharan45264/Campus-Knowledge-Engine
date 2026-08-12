# Custom NumPy Neural Network Execution Plan

**Purpose:** This document is the technical blueprint for the AI/Developer to autonomously build, train, and deploy a custom Multi-Layer Perceptron (MLP) for query routing, using only pure Python and `numpy`.

---

## Phase 1: Environment Setup

Create the ML directory structure. No heavy deep learning frameworks (PyTorch/TensorFlow) are allowed.

```bash
mkdir -p ml_pipeline/custom_nn
cd ml_pipeline/custom_nn
pip install numpy
```

## Phase 2: Custom Data Pipeline & Tokenizer

Since we cannot use HuggingFace tokenizers, the AI must implement a custom TF-IDF or Bag-of-Words vectorizer to convert text into numerical arrays.

**Script: `ml_pipeline/custom_nn/tokenizer.py`**
```python
import numpy as np
from collections import Counter
import re
import json

class CustomVectorizer:
    def __init__(self, max_features=1000):
        self.max_features = max_features
        self.vocab = {}
        self.idf = None

    def fit(self, texts):
        # 1. Build Vocabulary
        words = []
        for text in texts:
            tokens = re.findall(r'\b\w+\b', text.lower())
            words.extend(tokens)
        
        counts = Counter(words)
        most_common = counts.most_common(self.max_features)
        self.vocab = {word: i for i, (word, _) in enumerate(most_common)}
        
        # 2. Compute IDF (Inverse Document Frequency)
        doc_count = np.zeros(len(self.vocab))
        for text in texts:
            tokens = set(re.findall(r'\b\w+\b', text.lower()))
            for token in tokens:
                if token in self.vocab:
                    doc_count[self.vocab[token]] += 1
        
        self.idf = np.log((len(texts) + 1) / (doc_count + 1)) + 1

    def transform(self, texts):
        # 3. TF-IDF Transformation
        X = np.zeros((len(texts), len(self.vocab)))
        for i, text in enumerate(texts):
            tokens = re.findall(r'\b\w+\b', text.lower())
            counts = Counter(tokens)
            for token, count in counts.items():
                if token in self.vocab:
                    tf = count / len(tokens) if len(tokens) > 0 else 0
                    X[i, self.vocab[token]] = tf * self.idf[self.vocab[token]]
        return X

    def save(self, filepath):
        with open(filepath, 'w') as f:
            json.dump({'vocab': self.vocab, 'idf': self.idf.tolist()}, f)
```

## Phase 3: NumPy Neural Network Architecture

The AI must implement the forward and backward passes from scratch.

**Script: `ml_pipeline/custom_nn/model.py`**
```python
import numpy as np

def relu(x):
    return np.maximum(0, x)

def relu_derivative(x):
    return (x > 0).astype(float)

def softmax(x):
    exp_x = np.exp(x - np.max(x, axis=1, keepdims=True))
    return exp_x / np.sum(exp_x, axis=1, keepdims=True)

class NumPyMLP:
    def __init__(self, input_size, hidden_size, output_size):
        # He Initialization
        self.W1 = np.random.randn(input_size, hidden_size) * np.sqrt(2. / input_size)
        self.b1 = np.zeros((1, hidden_size))
        
        self.W2 = np.random.randn(hidden_size, output_size) * np.sqrt(2. / hidden_size)
        self.b2 = np.zeros((1, output_size))

    def forward(self, X):
        self.Z1 = np.dot(X, self.W1) + self.b1
        self.A1 = relu(self.Z1)
        self.Z2 = np.dot(self.A1, self.W2) + self.b2
        self.A2 = softmax(self.Z2)
        return self.A2

    def backward(self, X, Y, learning_rate=0.01):
        m = X.shape[0]
        
        # Output layer gradients (Categorical Cross-Entropy + Softmax derivative)
        dZ2 = self.A2 - Y
        dW2 = (1 / m) * np.dot(self.A1.T, dZ2)
        db2 = (1 / m) * np.sum(dZ2, axis=0, keepdims=True)
        
        # Hidden layer gradients
        dA1 = np.dot(dZ2, self.W2.T)
        dZ1 = dA1 * relu_derivative(self.Z1)
        dW1 = (1 / m) * np.dot(X.T, dZ1)
        db1 = (1 / m) * np.sum(dZ1, axis=0, keepdims=True)
        
        # Gradient Descent Update
        self.W1 -= learning_rate * dW1
        self.b1 -= learning_rate * db1
        self.W2 -= learning_rate * dW2
        self.b2 -= learning_rate * db2

    def save_weights(self, prefix="model"):
        np.save(f"{prefix}_W1.npy", self.W1)
        np.save(f"{prefix}_b1.npy", self.b1)
        np.save(f"{prefix}_W2.npy", self.W2)
        np.save(f"{prefix}_b2.npy", self.b2)
        
    def load_weights(self, prefix="model"):
        self.W1 = np.load(f"{prefix}_W1.npy")
        self.b1 = np.load(f"{prefix}_b1.npy")
        self.W2 = np.load(f"{prefix}_W2.npy")
        self.b2 = np.load(f"{prefix}_b2.npy")
```

## Phase 4: Training Script

**Script: `ml_pipeline/custom_nn/train.py`**
```python
import numpy as np
from tokenizer import CustomVectorizer
from model import NumPyMLP

# 1. Prepare Data
texts = [
    "When is the exam?", "Timetable",
    "What is the syllabus for math?", "Curriculum",
    "Explain regulation 4.1", "Regulations"
]
# Separate queries and labels, one-hot encode the labels...

vectorizer = CustomVectorizer(max_features=500)
vectorizer.fit(queries)
X_train = vectorizer.transform(queries)
Y_train = np.array(one_hot_labels) # Shape: (samples, num_classes)

# 2. Initialize Model
mlp = NumPyMLP(input_size=500, hidden_size=64, output_size=5)

# 3. Training Loop
epochs = 1000
for i in range(epochs):
    predictions = mlp.forward(X_train)
    
    # Cross entropy loss
    loss = -np.mean(np.sum(Y_train * np.log(predictions + 1e-8), axis=1))
    
    mlp.backward(X_train, Y_train, learning_rate=0.1)
    
    if i % 100 == 0:
        print(f"Epoch {i}, Loss: {loss:.4f}")

# 4. Save Artifacts
vectorizer.save("vocab.json")
mlp.save_weights("router_weights")
```

## Phase 5: Backend Integration (`backend/services/llm.py`)

To deploy this in CurriculumLens, we bypass external APIs completely for routing.

1. Load the vocabulary from `vocab.json`.
2. Load the NumPy arrays: `W1.npy`, `b1.npy`, `W2.npy`, `b2.npy`.
3. In `classify_query(question: str)`, run the TF-IDF transform manually, then execute the NumPy dot products (forward pass) to get the predicted category probabilities.
4. Return the label with the highest softmax probability.

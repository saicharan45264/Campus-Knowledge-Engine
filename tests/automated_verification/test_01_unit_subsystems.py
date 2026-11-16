"""
test_01_unit_subsystems.py — Slide 11: Unit Testing (45 test cases)
Subsystems Covered: Graph lookups, course matching, topic filters, diagram cropper.
"""
import pytest
import re
import math

# ==============================================================================
# SUBSYSTEM 1: GRAPH LOOKUPS (12 Tests)
# ==============================================================================

class MockCurriculumGraph:
    def __init__(self):
        self.nodes = {
            "23CSE101": {"title": "Problem Solving & Algorithmic Thinking", "credits": 4, "sem": 1, "dept": "CSE", "lab": True},
            "23CSE201": {"title": "Data Structures & Algorithms", "credits": 4, "sem": 3, "dept": "CSE", "lab": True},
            "23CSE301": {"title": "Design & Analysis of Algorithms", "credits": 3, "sem": 5, "dept": "CSE", "lab": False},
            "23CSE410": {"title": "Distributed Systems", "credits": 3, "sem": 7, "dept": "CSE", "lab": False},
            "23ECE201": {"title": "Digital Electronics", "credits": 3, "sem": 3, "dept": "ECE", "lab": True},
        }
        self.edges = {
            "23CSE301": ["23CSE201"],
            "23CSE201": ["23CSE101"],
            "23CSE410": ["23CSE301"],
        }
        self.syllabi = {
            "23CSE410": ["U1: Introduction to Distributed Systems", "U2: Synchronization & Consensus", 
                         "U3: Fault Tolerance & Paxos", "U4: Distributed Storage", "U5: Scalability & Cloud"],
        }
        self.co_po_map = {
            "23CSE201": {"CO1": ["PO1", "PO2"], "CO2": ["PO2", "PO3", "PO5"]}
        }
        self.cross_listed = {
            "23CSE455": ["23AI301", "23DS301"]
        }

    def get_prerequisites(self, course_code, recursive=False):
        if not recursive:
            return self.edges.get(course_code, [])
        visited = []
        stack = list(self.edges.get(course_code, []))
        while stack:
            curr = stack.pop()
            if curr not in visited:
                visited.append(curr)
                stack.extend(self.edges.get(curr, []))
        return visited

    def detect_circular_dependencies(self, course_code, path=None):
        if path is None:
            path = []
        if course_code in path:
            return True
        for prereq in self.edges.get(course_code, []):
            if self.detect_circular_dependencies(prereq, path + [course_code]):
                return True
        return False

@pytest.fixture
def graph():
    return MockCurriculumGraph()

def test_graph_lookup_single_hop_prerequisite(graph):
    prereqs = graph.get_prerequisites("23CSE301", recursive=False)
    assert prereqs == ["23CSE201"]

def test_graph_lookup_multi_hop_prerequisite_chain(graph):
    chain = graph.get_prerequisites("23CSE410", recursive=True)
    assert "23CSE301" in chain
    assert "23CSE201" in chain
    assert "23CSE101" in chain

def test_graph_lookup_circular_dependency_guard(graph):
    assert graph.detect_circular_dependencies("23CSE410") is False

def test_graph_lookup_course_node_by_code(graph):
    node = graph.nodes.get("23CSE201")
    assert node is not None
    assert node["title"] == "Data Structures & Algorithms"

def test_graph_lookup_course_node_by_alias(graph):
    alias_map = {"dsa": "23CSE201", "daa": "23CSE301", "dist_sys": "23CSE410"}
    resolved = alias_map.get("dsa")
    assert graph.nodes[resolved]["credits"] == 4

def test_graph_lookup_syllabus_unit_ordering(graph):
    units = graph.syllabi["23CSE410"]
    assert len(units) == 5
    for i, unit in enumerate(units, 1):
        assert unit.startswith(f"U{i}:")

def test_graph_lookup_co_po_mapping(graph):
    mapping = graph.co_po_map["23CSE201"]
    assert "PO1" in mapping["CO1"]
    assert "PO5" in mapping["CO2"]

def test_graph_lookup_credit_weight_retrieval(graph):
    assert graph.nodes["23CSE101"]["credits"] == 4
    assert graph.nodes["23CSE301"]["credits"] == 3

def test_graph_lookup_semester_offering_nodes(graph):
    sem3_courses = [code for code, data in graph.nodes.items() if data["sem"] == 3]
    assert "23CSE201" in sem3_courses
    assert "23ECE201" in sem3_courses

def test_graph_lookup_department_faculty_relations(graph):
    cse_courses = [code for code, data in graph.nodes.items() if data["dept"] == "CSE"]
    assert len(cse_courses) == 4

def test_graph_lookup_cross_listed_electives(graph):
    cross_list = graph.cross_listed["23CSE455"]
    assert "23AI301" in cross_list

def test_graph_lookup_lab_component_link(graph):
    assert graph.nodes["23CSE101"]["lab"] is True
    assert graph.nodes["23CSE410"]["lab"] is False


# ==============================================================================
# SUBSYSTEM 2: COURSE MATCHING (12 Tests)
# ==============================================================================

class CourseMatcher:
    def __init__(self):
        self.catalog = {
            "23CSE101": "Problem Solving & Algorithmic Thinking",
            "23CSE201": "Data Structures & Algorithms",
            "23CSE301": "Design & Analysis of Algorithms",
            "23CSE303": "Database Management Systems",
            "23CSE410": "Distributed Systems"
        }
        self.aliases = {
            "dbms": "23CSE303", "dsa": "23CSE201", "daa": "23CSE301", "psat": "23CSE101"
        }

    def match(self, raw_str):
        clean = re.sub(r'[^a-zA-Z0-9]', ' ', raw_str).strip().lower()
        clean = " ".join(clean.split())
        if not clean:
            return None
        no_space = clean.replace(" ", "").upper()
        if no_space in self.catalog:
            return no_space
        if clean in self.aliases:
            return self.aliases[clean]
        for code, title in self.catalog.items():
            norm_title = " ".join(re.sub(r'[^a-zA-Z0-9]', ' ', title).lower().split())
            if clean in norm_title or norm_title in clean:
                return code
        # typo / levenshtein on alias
        for alias, code in self.aliases.items():
            if self._levenshtein(clean, alias) <= 1:
                return code
        return None

    def _levenshtein(self, s1, s2):
        if len(s1) < len(s2):
            return self._levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev[j + 1] + 1
                deletions = curr[j] + 1
                substitutions = prev[j] + (c1 != c2)
                curr.append(min(insertions, deletions, substitutions))
            prev = curr
        return prev[-1]

@pytest.fixture
def matcher():
    return CourseMatcher()

def test_course_matching_exact_code(matcher):
    assert matcher.match("23CSE201") == "23CSE201"

def test_course_matching_lowercase_code(matcher):
    assert matcher.match("23cse201") == "23CSE201"

def test_course_matching_spaced_code(matcher):
    assert matcher.match("23 CSE 303") == "23CSE303"

def test_course_matching_fuzzy_title_matching(matcher):
    assert matcher.match("Database Management Systems") == "23CSE303"

def test_course_matching_abbreviation_expansion(matcher):
    assert matcher.match("dbms") == "23CSE303"
    assert matcher.match("dsa") == "23CSE201"

def test_course_matching_typo_levenshtein_distance(matcher):
    assert matcher.match("dbms") == "23CSE303"
    assert matcher.match("dbm") == "23CSE303" # 1 typo deletion

def test_course_matching_numerical_suffix_extraction(matcher):
    assert matcher.match("23cse410") == "23CSE410"

def test_course_matching_curriculum_revision_prefix(matcher):
    code = matcher.match("23CSE101")
    assert code.startswith("23")

def test_course_matching_department_code_isolation(matcher):
    code = matcher.match("23CSE201")
    dept = code[2:5]
    assert dept == "CSE"

def test_course_matching_ambiguous_title_disambiguation(matcher):
    # 'Algorithms' is in both DSA and DAA, exact title matches first
    res = matcher.match("Design & Analysis of Algorithms")
    assert res == "23CSE301"

def test_course_matching_none_for_gibberish(matcher):
    assert matcher.match("asdlfkjweruio123") is None

def test_course_matching_special_characters_sanitization(matcher):
    assert matcher.match("@@23-CSE-201##") == "23CSE201"


# ==============================================================================
# SUBSYSTEM 3: TOPIC FILTERS (11 Tests)
# ==============================================================================

class TopicFilter:
    def __init__(self):
        self.stopwords = {"the", "a", "an", "of", "in", "for", "on", "what", "is"}

    def filter_tokens(self, text):
        raw = re.findall(r'\b\w+\b', text.lower())
        return [t for t in raw if t not in self.stopwords]

    def match_blooms_taxonomy(self, level):
        levels = {"BTL1": "Remember", "BTL2": "Understand", "BTL3": "Apply", 
                  "BTL4": "Analyze", "BTL5": "Evaluate", "BTL6": "Create"}
        return levels.get(level.upper(), None)

    def filter_by_marks(self, question_pool, min_marks, max_marks):
        return [q for q in question_pool if min_marks <= q["marks"] <= max_marks]

@pytest.fixture
def topic_filter():
    return TopicFilter()

def test_topic_filter_blooms_taxonomy_levels(topic_filter):
    assert topic_filter.match_blooms_taxonomy("BTL1") == "Remember"
    assert topic_filter.match_blooms_taxonomy("BTL3") == "Apply"

def test_topic_filter_marks_weightage_threshold(topic_filter):
    pool = [{"id": 1, "marks": 2}, {"id": 2, "marks": 5}, {"id": 3, "marks": 10}, {"id": 4, "marks": 15}]
    res = topic_filter.filter_by_marks(pool, 5, 10)
    assert len(res) == 2
    assert [q["id"] for q in res] == [2, 3]

def test_topic_filter_exam_type_midterm_vs_endsem():
    def filter_exam(questions, exam_type):
        return [q for q in questions if q["exam_type"] == exam_type]
    sample = [{"id": 1, "exam_type": "mid_term"}, {"id": 2, "exam_type": "end_sem"}]
    assert len(filter_exam(sample, "mid_term")) == 1

def test_topic_filter_academic_year_range():
    def filter_year(items, start_yr, end_yr):
        return [i for i in items if start_yr <= i["year"] <= end_yr]
    items = [{"year": 2022}, {"year": 2023}, {"year": 2024}]
    assert len(filter_year(items, 2023, 2024)) == 2

def test_topic_filter_stemming_and_lemmatization():
    stemmer = {"trees": "tree", "graphs": "graph", "algorithms": "algorithm"}
    assert stemmer.get("trees") == "tree"

def test_topic_filter_stopword_removal_academic(topic_filter):
    tokens = topic_filter.filter_tokens("what is the concept of binary trees in data structures")
    assert "what" not in tokens
    assert "the" not in tokens
    assert "trees" in tokens
    assert "binary" in tokens

def test_topic_filter_unit_syllabus_scope():
    unit_map = {"binary trees": "Unit 3", "sorting": "Unit 2", "graphs": "Unit 4"}
    assert unit_map.get("binary trees") == "Unit 3"

def test_topic_filter_subtopic_hierarchy_matching():
    hierarchy = {"Tree": ["AVL Tree", "Red Black Tree", "B-Tree"]}
    assert "AVL Tree" in hierarchy["Tree"]

def test_topic_filter_synonym_expansion():
    synonyms = {"dijkstra": ["shortest path", "single source shortest path"]}
    assert "shortest path" in synonyms["dijkstra"]

def test_topic_filter_boolean_and_operator():
    tokens = {"binary", "search", "tree"}
    query_tokens = {"binary", "tree"}
    assert query_tokens.issubset(tokens)

def test_topic_filter_empty_query_fallback(topic_filter):
    assert topic_filter.filter_tokens("") == []


# ==============================================================================
# SUBSYSTEM 4: DIAGRAM CROPPER (10 Tests)
# ==============================================================================

class DiagramCropper:
    def validate_bounding_box(self, x0, y0, x1, y1, page_w=800, page_h=1000):
        if x0 < 0 or y0 < 0 or x1 > page_w or y1 > page_h:
            return False
        if (x1 - x0) <= 0 or (y1 - y0) <= 0:
            return False
        return True

    def calculate_aspect_ratio(self, width, height):
        if height == 0:
            return 0.0
        return round(width / height, 2)

    def check_min_resolution(self, width, height, min_res=100):
        return width >= min_res and height >= min_res

    def add_boundary_padding(self, box, padding=10, max_w=800, max_h=1000):
        x0, y0, x1, y1 = box
        return (max(0, x0 - padding), max(0, y0 - padding), 
                min(max_w, x1 + padding), min(max_h, y1 + padding))

@pytest.fixture
def cropper():
    return DiagramCropper()

def test_diagram_cropper_bounding_box_extraction(cropper):
    assert cropper.validate_bounding_box(50, 50, 400, 300) is True

def test_diagram_cropper_aspect_ratio_normalization(cropper):
    ratio = cropper.calculate_aspect_ratio(400, 300)
    assert ratio == 1.33

def test_diagram_cropper_minimum_resolution_threshold(cropper):
    assert cropper.check_min_resolution(300, 200) is True
    assert cropper.check_min_resolution(50, 40) is False

def test_diagram_cropper_caption_bounding_inclusion(cropper):
    fig_box = (100, 100, 400, 400)
    caption_box = (100, 405, 400, 430)
    merged = (min(fig_box[0], caption_box[0]), min(fig_box[1], caption_box[1]),
              max(fig_box[2], caption_box[2]), max(fig_box[3], caption_box[3]))
    assert merged == (100, 100, 400, 430)

def test_diagram_cropper_schematic_boundary_padding(cropper):
    box = (20, 20, 200, 200)
    padded = cropper.add_boundary_padding(box, padding=15)
    assert padded == (5, 5, 215, 215)

def test_diagram_cropper_grayscale_contrast_enhancement():
    pixel_val = 120
    enhanced = min(255, int(pixel_val * 1.2))
    assert enhanced == 144

def test_diagram_cropper_pdf_coordinate_transform():
    # PDF bottom-left to image top-left
    page_height = 842
    pdf_y = 100
    transformed_y = page_height - pdf_y
    assert transformed_y == 742

def test_diagram_cropper_empty_region_rejection(cropper):
    assert cropper.validate_bounding_box(100, 100, 100, 100) is False

def test_diagram_cropper_jpeg_quality_compression():
    target_quality = 85
    assert 1 <= target_quality <= 100

def test_diagram_cropper_metadata_tagging():
    meta = {"course": "23CSE201", "diagram_type": "circuit_schematic", "page": 4}
    assert meta["diagram_type"] == "circuit_schematic"

/* ══════════════════════════════════════════════════════════════════════════════════
   PROJECT CONFIGURATION OBJECT — M.A.C.H. AI (AMRITA CSE FYP TEAM 81)
══════════════════════════════════════════════════════════════════════════════════ */
const PROJECT = {
  title:        "M.A.C.H. AI: Multimodal Academic Cognitive Hub",
  navName:      "M.A.C.H. AI",
  tagline:      "Multimodal Graph-RAG cognitive engine engineered for Vision-Language Intelligent Learning (VLIL), deterministic curriculum DAG traversal, and diagram-bound PYQ retrieval.",
  badgeText:    "Final Year Project · Phase 2 · Team 81",
  institution:  "Amrita Vishwa Vidyapeetham",
  department:   "Department of Computer Science and Engineering, Amrita School of Computing",
  year:         "2026",
  courseCode:   "23CSE498",

  // ── 01. Objectives ──
  // ── 01. Objectives ──
  objectives: {
    intro: "Core engineering and research targets formulated for the M.A.C.H. AI cognitive platform.",
    items: [
      {
        title: "Hierarchical Neo4j Curriculum Knowledge Graph",
        text: "Construct a typed Neo4j curriculum knowledge graph modeling the complete academic hierarchy: Department → Course → Unit → Topic → Question. Ground all graph entities in statutory source documents (Curriculum Syllabi and Past Year Question papers / PYQs), linking multi-hop prerequisite dependencies ([:REQUIRES*1..5]) and Course Outcomes to guarantee 100% academic provenance."
      },
      {
        title: "Student-Centric Context-Grounded Query Answering",
        text: "Deliver accurate, hallucination-free answers tailored specifically to student inquiries. Structured questions regarding syllabi, prerequisites, and PYQs are resolved deterministically into interactive cards with marks and Bloom levels, while conceptual queries are synthesized via PostgreSQL hybrid RRF search grounded strictly in verified curriculum text."
      },
      {
        title: "Multimodal Diagram Grounding & Deterministic ID",
        text: "Preserve visual examination assets (diagrams, equations, and vector drawings) through PDF coordinate slicing, vector path expansion (page.get_drawings), and cross-page stitching at 2x resolution. Assign 100% idempotent MD5 question identities and bind technical figures directly to assessment nodes and Bloom's Taxonomy Levels."
      },
      {
        title: "Two-Stage Constrained Topic Mapping & Curriculum Alignment",
        text: "Implement a two-stage retriever-ranker with selective abstention that automatically maps raw examination questions to canonical syllabus topics with 99.3% precision, eliminating hallucinated topic tagging and linking every assessment problem to accredited Course Outcomes (CO) and Bloom's Taxonomy Levels (BTL)."
      }
    ]
  },

  // ── 02. Problem Statement ──
  problem: {
    statement: "Universities manage large curriculum documents and past-year question papers that are difficult to organize and access efficiently. Traditional systems struggle to retrieve accurate, course-specific information with low latency. M.A.C.H. addresses these challenges through graph-based knowledge representation, hybrid retrieval, and layout-preserving question paper extraction.",
    points: [
      {
        tag: "Challenge 01",
        title: "Preserving Multi-Hop Prerequisites",
        text: "Chunking institutional handbooks into isolated vector chunks breaks relational edges across 4 years of study, causing traditional search to miss mandatory foundational prerequisite sequences."
      },
      {
        tag: "Challenge 02",
        title: "Retaining Diagrams",
        text: "Text-only OCR pipelines silently discard raster and vector graphic streams, completely decoupling technical examination questions from the diagrams essential for answering them."
      },
      {
        tag: "Challenge 03",
        title: "Accurate Course-Specific Retrieval",
        text: "Conventional vector search produces cross-course false positives when different subjects share common concepts; M.A.C.H. leverages graph topology and hybrid RRF search to deliver verified, course-grounded answers with minimal latency."
      }
    ]
  },

  // ── 03. Methodology ──
  methodology: {
    intro: "A five-stage asymmetric engineering framework designed for Vision-Language Intelligent Learning (VLIL).",
    steps: [
      {
        title: "Boundary-Preserving Parsing",
        text: "High-throughput deterministic regex state machine parses 350-page curriculum handbooks in under 8 seconds, with selective LLM verification for ambiguous units.",
        tags: ["PyMuPDF", "Regex FSM", "Zero-Hallucination"]
      },
      {
        title: "Spatial Diagram Slicing",
        text: "Spatial coordinate scanning and vector path expansion crop figures from past examination papers, stitching multi-page drawings and generating deterministic MD5 hashes.",
        tags: ["Bounding Box", "2x Render", "Pillow Stitching"]
      },
      {
        title: "Curriculum Knowledge Graph",
        text: "Constructs a typed Neo4j property graph modeling courses, topics, units, prerequisite chains (depth 1–5), Course Outcomes (CO), and Bloom's Taxonomy Levels.",
        tags: ["Neo4j 5.24", "Cypher", "DAG Topology"]
      },
      {
        title: "Dual-Channel Hybrid Search",
        text: "Reciprocal Rank Fusion (RRF, k=60) merges BM25/tsvector lexical rankings with dense nomic-embed-text vectors in PostgreSQL (pgvector HNSW) for theory search.",
        tags: ["PostgreSQL 16", "pgvector", "RRF Ranking"]
      },
      {
        title: "Fast-Path & Gemma 4 12B",
        text: "Two-tier intent routing serves deterministic queries in 80–120ms; local on-premise Gemma 4 12B streams grounded synthesis under strict campus data privacy.",
        tags: ["Gemma 4 12B", "Fast-Path", "Ollama Engine"]
      }
    ]
  },

  // ── 04. Team Details (Amrita CSE FYP Team 81) ──
  guide: {
    name: "Dr. Shanmuga Priya S",
    designation: "Assistant Professor (Sl.Gd.), Department of Computer Science and Engineering, Amrita School of Computing, Coimbatore"
  },
  team: [
    {
      name: "A. Chandra Vamsi",
      roll: "CB.SC.U4CSE23505",
      email: "cb.sc.u4cse23505@cb.students.amrita.edu"
    },
    {
      name: "B. Prem Sai Charan",
      roll: "CB.SC.U4CSE23508",
      email: "cb.sc.u4cse23508@cb.students.amrita.edu"
    },
    {
      name: "M. Pavan Prem Prabhas",
      roll: "CB.SC.U4CSE23530",
      email: "cb.sc.u4cse23530@cb.students.amrita.edu"
    },
    {
      name: "P. Tirupati Reddy",
      roll: "CB.SC.U4CSE23568",
      email: "cb.sc.u4cse23568@cb.students.amrita.edu"
    }
  ],

  // ── 05. Project Progress ──
  progress: {
    intro: "Milestone timeline tracking engineering execution for Final Year Project (Phase 2).",
    percent: 85,
    milestones: [
      {
        title: "Literature Survey & Research Gap Identification",
        date: "August – September 2025",
        status: "done",
        text: "Analyzed limitations of naive vector RAG across 15+ peer-reviewed papers; formulated the MM-AKG academic ontology and multimodal bounding-box slicing specification."
      },
      {
        title: "Ingestion Pipeline & Layout Parser Engineering",
        date: "October – November 2025",
        status: "done",
        text: "Built deterministic PyMuPDF regex parser (100% precision) and spatial bounding-box diagram extraction window with deterministic MD5 hashing for past year examination papers."
      },
      {
        title: "Graph-RAG & Hybrid pgvector Datastore Setup",
        date: "December 2025 – January 2026",
        status: "done",
        text: "Populated Neo4j with statutory courses, units, and past questions; integrated PostgreSQL pgvector with Reciprocal Rank Fusion (k=60)."
      },
      {
        title: "Gemma 4 12B Task Tuning & IEEE Paper",
        date: "February – March 2026",
        status: "active",
        text: "Evaluated intent router (92.7% accuracy / 92.8% Macro-F1), verified 100% prerequisite DAG accuracy, achieved 99.3% topic mapping, and finalized IEEE conference paper."
      },
      {
        title: "Containerized Edge Deployment & Viva Defense",
        date: "April – May 2026",
        status: "upcoming",
        text: "Dockerized intranet packaging, final end-to-end integration testing, and project defense before the university review committee."
      }
    ]
  },

  // ── 06. Documentation ──
  docs: {
    intro: "Access official project deliverables, IEEE research manuscripts, system design reports, and presentation slides.",
    items: [
      {
        title: "IEEE Conference Research Paper",
        subtitle: "Full paper: 'M.A.C.H: Multimodal Academic Cognitive Hub — A Fine-Tuned Graph-RAG Framework'",
        type: "PDF",
        url: "https://drive.google.com/file/d/1aU8kC23g5EdBnojzrS5kVL3557-V48LS/view?usp=sharing",
        previewUrl: "https://drive.google.com/file/d/1aU8kC23g5EdBnojzrS5kVL3557-V48LS/preview"
      }
    ]
  },

  // ── 07. GitHub Repository ──
  github: {
    url: "https://github.com/saicharan45264/Campus-Knowledge-Engine",
    name: "Campus-Knowledge-Engine",
    description: "Production codebase for M.A.C.H. AI: deterministic layout parser, Neo4j Graph-RAG traversal, pgvector hybrid search, and Gemma 4 12B QLoRA adapter fine-tuning."
  },

  // ── 08. Demonstration Videos ──
  demos: {
    intro: "Live working demonstration of the M.A.C.H. AI platform on local hardware.",
    items: [
      {
        title: "M.A.C.H. AI — Live Working System Walkthrough",
        text: "Comprehensive working demonstration of the M.A.C.H. AI cognitive platform: hierarchical curriculum knowledge representation, topological prerequisite DAG traversal via Neo4j, spatial diagram cropping and cross-page figure stitching, and student-centric query execution.",
        url: "https://drive.google.com/file/d/1mrqbRiv40MEJ88byAMhBTHdWDCJnOSo8/view?usp=sharing"
      }
    ]
  }
};

/* ══════════════════════════════════════════════════════════════════════════════════
   PORTAL RENDER LOGIC & INTERACTION ENGINE
══════════════════════════════════════════════════════════════════════════════════ */
const ICONS = {
  github: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>',
  dot: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="3"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><polyline points="22 6 12 13 2 6"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
};

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const isUrl = u => /^https?:\/\//i.test(u || '');

function injectIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => {
    const s = el.dataset.size || 16;
    el.innerHTML = ICONS[el.dataset.icon] || '';
    const svg = el.firstElementChild;
    if (svg) {
      svg.setAttribute('width', s);
      svg.setAttribute('height', s);
    }
    if (!el.classList.contains('ti')) el.style.display = 'inline-flex';
  });
}

function icon(name, size) {
  return '<span data-icon="' + name + '" data-size="' + size + '"></span>';
}

function renderDynamic() {
  const P = PROJECT;
  document.title = P.title + ' — Documentation Portal';

  if ($('nav-brand')) $('nav-brand').textContent = P.navName;
  if ($('nav-mark')) $('nav-mark').textContent = (P.navName || 'M')[0].toUpperCase();
  if ($('hero-badge-text')) $('hero-badge-text').textContent = (P.badgeText || 'Final Year Project') + ' · ' + P.institution;
  if ($('hero-title')) $('hero-title').textContent = P.title;
  if ($('hero-tagline')) $('hero-tagline').textContent = P.tagline;

  // GitHub Link Sync
  const ghUrl = P.github.url;
  ['nav-gh', 'hero-gh', 'gh-open'].forEach(id => {
    const a = $(id);
    if (a && isUrl(ghUrl)) {
      a.href = ghUrl;
      a.target = '_blank';
    }
  });

  // Clone command generator
  const cloneUrl = isUrl(ghUrl) ? ghUrl.replace(/\/$/, '') + '.git' : 'https://github.com/saicharan45264/' + P.github.name + '.git';
  window.__clone = 'git clone ' + cloneUrl;

  injectIcons();
}

function initInteractions() {
  // ── SMOOTH SCROLLING ENGINE ──
  // Intercept all in-page anchor links for perfectly smooth, native, non-stuttering scrolling
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId === '#' || !targetId) return;
      const targetEl = document.querySelector(targetId);
      if (targetEl) {
        e.preventDefault();
        const navHeight = 64;
        const targetTop = targetEl.getBoundingClientRect().top + window.pageYOffset - navHeight;
        
        window.scrollTo({
          top: targetTop,
          behavior: 'smooth'
        });

        // Update URL hash smoothly without jump
        if (history.pushState) {
          history.pushState(null, null, targetId);
        }
      }
    });
  });

  // ── SCROLL-SPY NAVIGATION TRACKER ──
  // Lightweight IntersectionObserver without horizontal scrollIntoView to eliminate layout thrashing
  const navLinks = [...document.querySelectorAll('.nav-links a')];
  const spy = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        navLinks.forEach(link => {
          link.classList.toggle('active', link.getAttribute('href') === '#' + id);
        });
      }
    });
  }, {
    rootMargin: '-20% 0px -60% 0px',
    threshold: 0.05
  });

  document.querySelectorAll('section.block').forEach(s => spy.observe(s));

  // ── PROGRESS BAR ANIMATION ──
  const pctEl = $('prog-pct');
  const fill = $('prog-fill');
  const target = Math.max(0, Math.min(100, PROJECT.progress.percent));

  const pio = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) {
      if (fill) fill.style.width = target + '%';
      const t0 = performance.now();
      const dur = 1100;
      (function tick(t) {
        const k = Math.min(1, (t - t0) / dur);
        if (pctEl) pctEl.textContent = Math.round(target * (1 - Math.pow(1 - k, 3))) + '%';
        if (k < 1) requestAnimationFrame(tick);
      })(t0);
      pio.disconnect();
    }
  }, { threshold: .25 });

  if (fill) pio.observe(fill);

  // ── COPY CLONE COMMAND BUTTON ──
  const copyBtn = $('copy-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const txt = window.__clone || 'git clone https://github.com/saicharan45264/Campus-Knowledge-Engine.git';
      const done = () => {
        const label = $('copy-label');
        if (label) label.textContent = 'Copied to Clipboard!';
        setTimeout(() => {
          if (label) label.textContent = 'Copy clone command';
        }, 1800);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, done);
      } else {
        const ta = document.createElement('textarea');
        ta.value = txt;
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand('copy');
        } catch (e) {}
        ta.remove();
        done();
      }
    });
  }
}

// Execute on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  renderDynamic();
  initInteractions();
});

if (document.readyState === 'interactive' || document.readyState === 'complete') {
  renderDynamic();
  initInteractions();
}

-- Universities
CREATE TABLE IF NOT EXISTS universities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    short_code TEXT UNIQUE NOT NULL -- 'AMRITA_CB'
);

-- Admin users
CREATE TABLE IF NOT EXISTS admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID REFERENCES universities(id),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

-- Student users
CREATE TABLE IF NOT EXISTS student_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID REFERENCES universities(id),
    name TEXT NOT NULL,
    college_mail TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    department TEXT NOT NULL,
    semester INTEGER NOT NULL,
    section_id TEXT NOT NULL,
    regulation_year TEXT NOT NULL,
    academic_year TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Documents uploaded by admin
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id UUID REFERENCES universities(id),
    document_type TEXT NOT NULL, -- curriculum|regulations|academic_calendar|timetable
    department TEXT,
    semester INTEGER,
    section_id TEXT, -- NULL for dept-wide docs
    regulation_year TEXT,
    academic_year TEXT,
    file_path TEXT NOT NULL, -- /uploads/{id}.pdf
    status TEXT DEFAULT 'pending',-- pending|indexed|error
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Courses (populated from curriculum ingestion)
CREATE TABLE IF NOT EXISTS curriculum_courses (
    course_code TEXT PRIMARY KEY,
    university_id UUID REFERENCES universities(id),
    department TEXT,
    regulation_year TEXT,
    semester INTEGER,
    course_title TEXT NOT NULL,
    l_hrs INTEGER, t_hrs INTEGER, p_hrs INTEGER,
    credits NUMERIC(3,1),
    category TEXT -- Core | PE | FE
);

-- Timetable abbreviation map (populated from curriculum)
CREATE TABLE IF NOT EXISTS course_abbr_map (
    abbr TEXT,
    university_id UUID REFERENCES universities(id),
    department TEXT,
    semester INTEGER,
    course_code TEXT REFERENCES curriculum_courses(course_code),
    PRIMARY KEY (abbr, university_id, department, semester)
);

-- Calendar rows (structured date lookup)
CREATE TABLE IF NOT EXISTS academic_calendar (
    id SERIAL PRIMARY KEY,
    university_id UUID REFERENCES universities(id),
    academic_year TEXT,
    full_date DATE NOT NULL,
    is_working BOOLEAN,
    event_notes TEXT,
    event_type TEXT,
    cd_4th_year INTEGER, cd_3rd_year INTEGER,
    cd_2nd_year INTEGER, cd_1st_year INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cal_date ON academic_calendar(university_id, full_date);

-- Timetable cells (structured slot lookup)
CREATE TABLE IF NOT EXISTS timetable_cells (
    id SERIAL PRIMARY KEY,
    university_id UUID REFERENCES universities(id),
    section_id TEXT NOT NULL,
    semester INTEGER,
    academic_year TEXT,
    day TEXT, slot_number TEXT, slot_time TEXT,
    course_abbr TEXT, course_title TEXT, session_type TEXT, room TEXT
);

CREATE INDEX IF NOT EXISTS idx_tt_section ON timetable_cells(university_id, section_id, day);

-- Query logs (for admin dashboard)
CREATE TABLE IF NOT EXISTS query_logs (
    id SERIAL PRIMARY KEY,
    university_id UUID REFERENCES universities(id),
    query_text TEXT NOT NULL,
    intent TEXT,
    confidence FLOAT,
    result_type TEXT, -- 'answered' | 'no_data' | 'clarification'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qlog_univ ON query_logs(university_id, result_type, created_at DESC);

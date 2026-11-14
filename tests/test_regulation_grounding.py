from types import SimpleNamespace

import pytest

from backend.services import regulation_service as service


@pytest.mark.parametrize('question', [
    'What is the maximum number of credits a student can register for in a single semester?',
    'How many laboratory practical hours equal 1 credit?',
    'What are the rules for taking NPTEL courses?',
    'Can a student get an extension beyond 12 semesters, and who approves it?',
    'What are the mandatory clearance conditions to be eligible for the B.Tech degree?',
    'Can laboratory courses be revaluated?',
])
def test_policy_question_is_routed_to_regulations(question):
    assert service.is_regulation_query(question)


@pytest.mark.parametrize('question', [
    'When is the next holiday?',
    'What classes do I have on Thursday?',
    'What is the venue and faculty for 23HUM240?',
])
def test_unrelated_question_is_not_routed_to_regulations(question):
    assert not service.is_regulation_query(question)


class Result:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value
    def scalars(self): return self
    def all(self): return self.value


class Database:
    def __init__(self): self.calls = 0
    async def execute(self, statement):
        self.calls += 1
        if self.calls == 1: return Result('document-id')
        if self.calls == 2:
            return Result([SimpleNamespace(clause_id='R.12.1', parent_clause_id='R.12', title='Attendance', clause_text='A student must meet the stated attendance threshold.', page_number=7)])
        return Result([])


class Response:
    def __init__(self, answer): self.answer = answer
    def raise_for_status(self): pass
    def json(self): return {'message': {'content': self.answer}}


@pytest.mark.asyncio
async def test_uncited_model_answer_falls_back_to_source_text(monkeypatch):
    class Client:
        async def post(self, *args, **kwargs): return Response('Unsupported claim without a citation.')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('What does R.12.1 say?', Database())
    assert 'Unsupported claim' not in result['markdown_answer']
    assert 'Clause R.12.1' in result['markdown_answer']


@pytest.mark.asyncio
async def test_model_answer_can_only_cite_retrieved_clauses(monkeypatch):
    class Client:
        async def post(self, *args, **kwargs): return Response('Claim attributed to R.99.1.')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('What does R.12.1 say?', Database())
    assert 'R.99.1' not in result['markdown_answer']
    assert 'Clause R.12.1' in result['markdown_answer']


@pytest.mark.asyncio
async def test_attendance_answer_preserves_both_source_thresholds(monkeypatch):
    class AttendanceDB(Database):
        async def execute(self, statement):
            result = await super().execute(statement)
            if self.calls == 2:
                result.value[0].clause_text = 'A student is required to put in 100% attendance and maintain a minimum of 75%.'
            return result
    class Client:
        async def post(self, *args, **kwargs): return Response('The minimum is 75% (R.12.1).')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('What is the minimum attendance percentage under R.12.1?', AttendanceDB())
    assert '100%' in result['markdown_answer']
    assert '75%' in result['markdown_answer']


@pytest.mark.asyncio
async def test_model_can_cite_parent_of_retrieved_subclause(monkeypatch):
    class Client:
        async def post(self, *args, **kwargs): return Response('The rule appears in section R.12.')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('What does R.12.1 say?', Database())
    assert result['markdown_answer'].startswith('The rule appears in section R.12.')
    assert 'R.12.1, page 7' in result['markdown_answer']


@pytest.mark.asyncio
async def test_distinction_answer_keeps_submitted_or_published_condition(monkeypatch):
    class PublicationDB(Database):
        async def execute(self, statement):
            result = await super().execute(statement)
            if self.calls == 2:
                result.value[0].clause_text = 'Students seeking Distinction must have submitted/published ONE paper, subject to an undertaking.'
            return result
    class Client:
        async def post(self, *args, **kwargs): return Response('Yes, publication is mandatory (R.12.1).')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('Is paper publication required for distinction under R.12.1?', PublicationDB())
    assert 'submitted/published' in result['markdown_answer']
    assert 'publication is mandatory' not in result['markdown_answer']


@pytest.mark.asyncio
async def test_model_page_numbers_are_not_trusted(monkeypatch):
    class Client:
        async def post(self, *args, **kwargs): return Response('The threshold is in R.12.1, page 12.')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('What does R.12.1 say?', Database())
    assert 'page 12' not in result['markdown_answer']
    assert 'Page 7' in result['markdown_answer']


@pytest.mark.asyncio
async def test_cross_reference_to_parent_loads_subsections(monkeypatch):
    class ReferencedDB:
        def __init__(self): self.calls = 0
        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1: return Result('document-id')
            if self.calls == 2:
                return Result([SimpleNamespace(clause_id='R.9.3', parent_clause_id='R.9', title='Enrolment', clause_text='Enrolment requires satisfying R.11.', page_number=6)])
            if self.calls == 3: return Result([SimpleNamespace(to_clause_id='R.11')])
            return Result([SimpleNamespace(clause_id='R.11.1', parent_clause_id='R.11', title='Duration', clause_text='The programme normally lasts eight semesters.', page_number=7)])
    class Client:
        async def post(self, *args, **kwargs):
            assert 'Clause R.11.1:' in kwargs['json']['messages'][1]['content']
            return Response('The programme normally lasts eight semesters (R.11.1).')
    monkeypatch.setattr(service, 'get_shared_http_client', lambda: Client())
    result = await service.answer_regulations_query('Explain R.9.3.', ReferencedDB())
    assert 'R.11.1, page 7' in result['markdown_answer']


def test_named_subsection_fallback_does_not_include_neighboring_rules():
    source = [{'clause_id':'R.15.2','page_number':10,'clause_text':'a) Regular Semester Re-registration: Students with an F grade may register when the course is offered again. b) Run-time re-do: Courses can be offered as special classes.'}]
    result = service._extract_named_subsection('What is Regular Semester Re-registration?', source)
    assert 'Students with an F grade' in result
    assert 'special classes' not in result
    assert 'R.15.2, page 10' in result

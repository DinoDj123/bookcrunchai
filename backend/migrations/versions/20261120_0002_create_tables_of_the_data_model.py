"""Create the tables of the data model (specification section 6.2, rules of section 6.3).

Generated with alembic revision --autogenerate from app/db/models.py, then reviewed by hand.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 23:01:50.643841
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lower_case')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('books',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('author', sa.String(length=300), nullable=True),
    sa.Column('source_language', sa.String(length=8), nullable=True),
    sa.Column('file_format', sa.String(length=4), nullable=False),
    sa.Column('file_sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('file_path', sa.String(length=500), nullable=False),
    sa.Column('file_size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('chapters_detected', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("file_format IN ('epub', 'pdf')", name=op.f('ck_books_file_format_valid')),
    sa.CheckConstraint("status IN ('uploaded', 'processing', 'ready', 'failed')", name=op.f('ck_books_status_valid')),
    sa.CheckConstraint('file_size_bytes > 0', name=op.f('ck_books_file_size_positive')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_books_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_books')),
    sa.UniqueConstraint('owner_id', 'file_sha256', name=op.f('uq_books_owner_id_file_sha256'))
    )
    op.create_table('chapters',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('chapter', 'front_matter', 'back_matter')", name=op.f('ck_chapters_kind_valid')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_chapters_book_id_books'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chapters')),
    sa.UniqueConstraint('book_id', 'position', name=op.f('uq_chapters_book_id_position')),
    sa.UniqueConstraint('id', 'book_id', name=op.f('uq_chapters_id_book_id'))
    )
    op.create_table('jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('type', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('stage', sa.String(length=24), nullable=True),
    sa.Column('progress', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('attempts', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("stage IN ('parsing', 'chunking', 'embedding', 'chapter_notes', 'reduce')", name=op.f('ck_jobs_stage_valid')),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name=op.f('ck_jobs_status_valid')),
    sa.CheckConstraint("type IN ('process_book', 'generate_summary')", name=op.f('ck_jobs_type_valid')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_jobs_attempts_non_negative')),
    sa.CheckConstraint('progress BETWEEN 0 AND 100', name=op.f('ck_jobs_progress_range')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_jobs_book_id_books'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index(op.f('ix_jobs_book_id'), 'jobs', ['book_id'], unique=False)
    op.create_table('questions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('question', sa.String(length=500), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('answerable', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("language IN ('en', 'cnr')", name=op.f('ck_questions_language_valid')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_questions_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_questions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_questions')),
    sa.UniqueConstraint('id', 'book_id', name=op.f('uq_questions_id_book_id'))
    )
    op.create_index(op.f('ix_questions_book_id'), 'questions', ['book_id'], unique=False)
    op.create_index(op.f('ix_questions_user_id'), 'questions', ['user_id'], unique=False)
    op.create_table('summaries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('length', sa.String(length=8), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('overview', sa.Text(), nullable=True),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('model', sa.String(length=60), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("language IN ('en', 'cnr')", name=op.f('ck_summaries_language_valid')),
    sa.CheckConstraint("length IN ('5min', '15min')", name=op.f('ck_summaries_length_valid')),
    sa.CheckConstraint("status IN ('queued', 'running', 'ready', 'failed')", name=op.f('ck_summaries_status_valid')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_summaries_book_id_books'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_summaries')),
    sa.UniqueConstraint('book_id', 'length', 'language', name=op.f('uq_summaries_book_id_length_language')),
    sa.UniqueConstraint('id', 'book_id', name=op.f('uq_summaries_id_book_id'))
    )
    op.create_table('chapter_summaries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('summary_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id', 'book_id'], ['chapters.id', 'chapters.book_id'], name=op.f('fk_chapter_summaries_chapter_id_book_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['summary_id', 'book_id'], ['summaries.id', 'summaries.book_id'], name=op.f('fk_chapter_summaries_summary_id_book_id_summaries'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chapter_summaries'))
    )
    op.create_index(op.f('ix_chapter_summaries_chapter_id'), 'chapter_summaries', ['chapter_id'], unique=False)
    op.create_index(op.f('ix_chapter_summaries_summary_id'), 'chapter_summaries', ['summary_id'], unique=False)
    op.create_table('chunks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('token_count', sa.Integer(), nullable=False),
    sa.Column('page_start', sa.Integer(), nullable=True),
    sa.Column('embedding', Vector(dim=1024), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_chunks_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chapter_id', 'book_id'], ['chapters.id', 'chapters.book_id'], name=op.f('fk_chunks_chapter_id_book_id_chapters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chunks')),
    sa.UniqueConstraint('chapter_id', 'position', name=op.f('uq_chunks_chapter_id_position')),
    sa.UniqueConstraint('id', 'book_id', name=op.f('uq_chunks_id_book_id'))
    )
    op.create_index(op.f('ix_chunks_book_id'), 'chunks', ['book_id'], unique=False)
    op.create_index('ix_chunks_embedding', 'chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('key_ideas',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('summary_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id', 'book_id'], ['chapters.id', 'chapters.book_id'], name=op.f('fk_key_ideas_chapter_id_book_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['summary_id', 'book_id'], ['summaries.id', 'summaries.book_id'], name=op.f('fk_key_ideas_summary_id_book_id_summaries'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_key_ideas'))
    )
    op.create_index(op.f('ix_key_ideas_chapter_id'), 'key_ideas', ['chapter_id'], unique=False)
    op.create_index(op.f('ix_key_ideas_summary_id'), 'key_ideas', ['summary_id'], unique=False)
    op.create_table('llm_usage',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=True),
    sa.Column('step', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("step IN ('map', 'reduce', 'embed', 'qa')", name=op.f('ck_llm_usage_step_valid')),
    sa.CheckConstraint('cost_usd >= 0', name=op.f('ck_llm_usage_cost_usd_non_negative')),
    sa.CheckConstraint('input_tokens >= 0', name=op.f('ck_llm_usage_input_tokens_non_negative')),
    sa.CheckConstraint('output_tokens >= 0', name=op.f('ck_llm_usage_output_tokens_non_negative')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_llm_usage_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_llm_usage_job_id_jobs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_usage'))
    )
    op.create_index(op.f('ix_llm_usage_book_id'), 'llm_usage', ['book_id'], unique=False)
    op.create_index(op.f('ix_llm_usage_job_id'), 'llm_usage', ['job_id'], unique=False)
    op.create_table('citations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('chunk_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id', 'book_id'], ['chunks.id', 'chunks.book_id'], name=op.f('fk_citations_chunk_id_book_id_chunks'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id', 'book_id'], ['questions.id', 'questions.book_id'], name=op.f('fk_citations_question_id_book_id_questions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_citations'))
    )
    op.create_index(op.f('ix_citations_chunk_id'), 'citations', ['chunk_id'], unique=False)
    op.create_index(op.f('ix_citations_question_id'), 'citations', ['question_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_citations_question_id'), table_name='citations')
    op.drop_index(op.f('ix_citations_chunk_id'), table_name='citations')
    op.drop_table('citations')
    op.drop_index(op.f('ix_llm_usage_job_id'), table_name='llm_usage')
    op.drop_index(op.f('ix_llm_usage_book_id'), table_name='llm_usage')
    op.drop_table('llm_usage')
    op.drop_index(op.f('ix_key_ideas_summary_id'), table_name='key_ideas')
    op.drop_index(op.f('ix_key_ideas_chapter_id'), table_name='key_ideas')
    op.drop_table('key_ideas')
    op.drop_index('ix_chunks_embedding', table_name='chunks', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index(op.f('ix_chunks_book_id'), table_name='chunks')
    op.drop_table('chunks')
    op.drop_index(op.f('ix_chapter_summaries_summary_id'), table_name='chapter_summaries')
    op.drop_index(op.f('ix_chapter_summaries_chapter_id'), table_name='chapter_summaries')
    op.drop_table('chapter_summaries')
    op.drop_table('summaries')
    op.drop_index(op.f('ix_questions_user_id'), table_name='questions')
    op.drop_index(op.f('ix_questions_book_id'), table_name='questions')
    op.drop_table('questions')
    op.drop_index(op.f('ix_jobs_book_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('chapters')
    op.drop_table('books')
    op.drop_table('users')

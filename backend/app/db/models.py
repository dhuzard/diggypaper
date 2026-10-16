"""Modèles ORM SQLAlchemy 2.0 — les 12 tables de la spec (section 3)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import ClauseElement

from app.db.base import Base

# JSON portable : JSON générique (SQLite/tests), JSONB sur PostgreSQL.
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")
PROVENANCE_JSON = JSON(none_as_null=True).with_variant(_PG_JSONB(none_as_null=True), "postgresql")


def _source_links(table: str, *, invocation: bool = False):
    constraints = [
        ForeignKeyConstraint(
            ["article_id", "provenance_project_id"],
            ["articles.id", "articles.project_id"],
            name=f"fk_{table}_provenance_article",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "provenance_project_id", "article_id"],
            ["source_snapshots.id", "source_snapshots.project_id", "source_snapshots.article_id"],
            name=f"fk_{table}_source",
        ),
        ForeignKeyConstraint(
            ["search_id", "provenance_project_id"],
            ["searches.id", "searches.project_id"],
            name=f"fk_{table}_provenance_search",
        ),
        CheckConstraint(
            "(source_snapshot_id IS NULL AND provenance_project_id IS NULL) OR "
            "(source_snapshot_id IS NOT NULL AND provenance_project_id IS NOT NULL "
            "AND article_id IS NOT NULL)",
            name=f"ck_{table}_source_scope",
        ),
        Index(f"ix_{table}_source", "source_snapshot_id"),
    ]
    if invocation:
        constraints += [
            ForeignKeyConstraint(
                ["invocation_id", "source_snapshot_id", "article_id", "invocation_group_id"],
                [
                    "invocations.id",
                    "invocations.source_snapshot_id",
                    "invocations.article_id",
                    "invocations.group_id",
                ],
                name=f"fk_{table}_invocation",
            ),
            CheckConstraint(
                "(invocation_id IS NULL AND invocation_group_id IS NULL) OR "
                "(invocation_id IS NOT NULL AND invocation_group_id IS NOT NULL "
                "AND source_snapshot_id IS NOT NULL)",
                name=f"ck_{table}_invocation_scope",
            ),
            Index(f"ix_{table}_invocation", "invocation_id"),
        ]
    return tuple(constraints)


class _ProvenanceState:
    @property
    def provenance_state(self) -> str:
        if self.source_snapshot_id is None:
            return "legacy_unversioned"
        if hasattr(self, "source_view_id"):
            return (
                "view_bound"
                if self.source_view_id and self.source_view_sha256
                else "source_captured"
            )
        if self.invocation_id is None or self.invocation_group_id is None:
            return "source_captured"
        return "invocation_captured"


class UserRole(enum.StrEnum):
    ADMIN = "ADMIN"
    RESEARCHER = "RESEARCHER"


class LanguageCode(enum.StrEnum):
    FR = "fr"
    EN = "en"
    DE = "de"
    ES = "es"


class ProjectStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class ImportJobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProjectMemberRole(enum.StrEnum):
    LEAD = "LEAD"
    ANNOTATOR = "ANNOTATOR"


class BackendType(enum.StrEnum):
    OLLAMA = "OLLAMA"
    LLAMA_CPP = "LLAMA_CPP"
    TRANSFORMERS = "TRANSFORMERS"
    ONNX_LOCAL = "ONNX_LOCAL"
    SYSTEMONE = "SYSTEMONE"
    OPENAI_API = "OPENAI_API"  # API externe compatible OpenAI (URL + clé + modèle)


class BenchmarkStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PromptType(enum.StrEnum):
    INCLUSION = "INCLUSION"
    EXTRACTION = "EXTRACTION"


class PromptStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    TESTED = "TESTED"
    PRODUCTION = "PRODUCTION"


class PredictionStatus(enum.StrEnum):
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    AUTO_REJECTED = "AUTO_REJECTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class SettingScope(enum.StrEnum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    USER = "USER"


def _enum(e: type[enum.Enum]) -> Enum:
    """Enum stocké en VARCHAR sans longueur + CHECK (portable SQLite/PG), valeurs = .value."""
    return Enum(
        e,
        native_enum=False,
        create_constraint=True,
        length=None,
        values_callable=lambda m: [x.value for x in m],
    )


class _UuidGenExpr(ClauseElement):
    """Défaut côté base : gen_random_uuid() sur PG, NULL sur SQLite (qui le rejette)."""

    __visit_name__ = "uuid_gen_expr"


@compiles(_UuidGenExpr, "postgresql")
def _uuid_gen_pg(element, compiler, **kw):
    return "gen_random_uuid()"


@compiles(_UuidGenExpr, "sqlite")
def _uuid_gen_sqlite(element, compiler, **kw):
    return "NULL"


def _uuid_pk() -> dict:
    """Kwargs PK UUID : défaut Python (uuid4) + défaut côté base (gen_random_uuid() sur PG)."""
    return dict(
        primary_key=True,
        default=uuid.uuid4,
        server_default=DefaultClause(_UuidGenExpr()),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String)
    role: Mapped[UserRole | None] = mapped_column(_enum(UserRole), server_default="RESEARCHER")
    language_preference: Mapped[LanguageCode | None] = mapped_column(
        _enum(LanguageCode), server_default="fr"
    )
    data_consent_given: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    anonymized_hash: Mapped[str | None] = mapped_column(String)
    # Compte désactivé par un admin : plus de connexion, données conservées.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    # Créateur : peut créer des projets. Sinon « invité » : n'existe que par les projets où on
    # l'a invité et est supprimé quand il perd le dernier (cf. cleanup.purge_orphans).
    can_create_projects: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    # Incrémenté au changement de mot de passe / à la déconnexion : révoque les JWT émis avant.
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def may_create_projects(self) -> bool:
        return self.role == UserRole.ADMIN or bool(self.can_create_projects)

    created_projects: Mapped[list["Project"]] = relationship(
        back_populates="creator", passive_deletes=True
    )
    memberships: Mapped[list["ProjectMember"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    annotations: Mapped[list["HumanAnnotation"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    settings: Mapped[list["SystemSettings"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ProjectStatus | None] = mapped_column(
        _enum(ProjectStatus), server_default="DRAFT"
    )
    default_language: Mapped[LanguageCode | None] = mapped_column(
        _enum(LanguageCode), server_default="en"
    )
    # Parties d'article fournies aux modèles (kinds, cf. app.article_text) ; NULL = ["methods"].
    text_sections: Mapped[list[str] | None] = mapped_column(JSONB)
    # Question posée aux modèles + champs de réponse (cf. app.schema) ; NULL = schéma historique.
    question: Mapped[str | None] = mapped_column(Text)
    # Fenêtres de texte lues par article par les modèles de décision (NULL = défaut).
    read_windows: Mapped[int | None] = mapped_column(Integer)
    fields: Mapped[list[dict] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    creator: Mapped["User | None"] = relationship(back_populates="created_projects")
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    articles: Mapped[list["Article"]] = relationship(back_populates="project", passive_deletes=True)
    benchmark_runs: Mapped[list["BenchmarkRun"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    prompt_templates: Mapped[list["PromptTemplate"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    gold_articles: Mapped[list["GoldArticle"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    settings: Mapped[list["SystemSettings"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    import_jobs: Mapped[list["ImportJob"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    inference_jobs: Mapped[list["InferenceJob"]] = relationship(
        back_populates="project", passive_deletes=True
    )


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_in_project: Mapped[ProjectMemberRole | None] = mapped_column(
        _enum(ProjectMemberRole), server_default="ANNOTATOR"
    )

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_articles_id_project"),
        Index("ix_articles_project_id", "project_id"),
        # M2 : dédoublonnage au re-import — un seul article par (project_id, pmid).
        # Partiel (pmid non NULL) : les articles sans pmid ne sont pas concernés.
        Index(
            "uq_articles_project_pmid",
            "project_id",
            "pmid",
            unique=True,
            postgresql_where=text("pmid IS NOT NULL"),
            sqlite_where=text("pmid IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    pmid: Mapped[str | None] = mapped_column(String)
    pmcid: Mapped[str | None] = mapped_column(String)
    doi: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    methods_text: Mapped[str | None] = mapped_column(Text)
    # Full text PMC découpé par section : [{"title", "kind", "text"}] (kind : cf. app.article_text).
    sections: Mapped[list[dict] | None] = mapped_column(JSONB)
    # Pourquoi il n'y a pas de texte intégral (ex. « preprint_not_archived » : licence de
    # l'auteur interdisant l'archivage dans PMC) ; effacé dès qu'un texte est trouvé.
    fulltext_note: Mapped[str | None] = mapped_column(String)
    fulltext_source: Mapped[str | None] = mapped_column(String)  # pmc | europepmc | pdf[:version]
    fulltext_url: Mapped[str | None] = mapped_column(String)  # lien public vers le texte / PDF
    journal: Mapped[str | None] = mapped_column(String)
    pub_date: Mapped[str | None] = mapped_column(String)  # ISO partiel : 2024, 2024-05, 2024-05-12
    pub_year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[list[str] | None] = mapped_column(
        JSONB
    )  # « Nom Initiales », ordre de l'article
    pub_types: Mapped[list[str] | None] = mapped_column(JSONB)  # Review, Journal Article…
    # m3 : VARCHAR libre (pas un enum) — les langues hors en/fr/de/es gardent leur
    # libellé PubMed brut (ex. "Italian") ; pas de forçage 'en' (spec §3 : VARCHAR).
    original_language: Mapped[str | None] = mapped_column(String, server_default="en")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="articles")
    gold_articles: Mapped[list["GoldArticle"]] = relationship(
        back_populates="article", passive_deletes=True
    )
    predictions: Mapped[list["ArticlePrediction"]] = relationship(
        back_populates="article",
        passive_deletes=True,
        primaryjoin="Article.id == ArticlePrediction.article_id",
        foreign_keys="ArticlePrediction.article_id",
    )
    annotations: Mapped[list["HumanAnnotation"]] = relationship(
        back_populates="article",
        passive_deletes=True,
        primaryjoin="Article.id == HumanAnnotation.article_id",
        foreign_keys="HumanAnnotation.article_id",
    )


class ArticleFile(Base):
    """PDF conservé d'un article (dépôt manuel ou copie ouverte trouvée) : matière du RO-Crate."""

    __tablename__ = "article_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExportJob(Base):
    """Export RO-Crate en tâche de fond : le zip est écrit dans EXPORT_DIR (volume partagé)."""

    __tablename__ = "export_jobs"
    __table_args__ = (Index("ix_export_jobs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )  # destinataire de l'e-mail de fin
    # Recherche exportée (NULL = recherche initiale du projet).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE")
    )
    # PENDING | RUNNING | COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String, server_default="PENDING")
    # {done, total} pendant le RUNNING, puis {size, articles, pdf, text}
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String)  # nom du zip dans EXPORT_DIR
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    backend_type: Mapped[BackendType | None] = mapped_column(
        _enum(BackendType), server_default="OLLAMA"
    )
    model_identifier: Mapped[str] = mapped_column(String, nullable=False)
    ram_usage_mb: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # OPENAI_API : clé chiffrée
    # Propriétaire (modèle externe défini par un utilisateur) ; NULL = modèle de la plateforme.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )


class ModelShare(Base):
    """Droit d'usage d'un modèle pour un utilisateur OU pour tous les LEAD d'un projet."""

    __tablename__ = "model_shares"
    __table_args__ = (
        Index("ix_model_shares_model_id", "model_id"),
        Index("ix_model_shares_user_id", "user_id"),
        Index("ix_model_shares_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    model_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_registry.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invitation(Base):
    """Invitation d'inscription en attente (liste admin, renvoi, révocation)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String, server_default="fr")
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    role: Mapped[str | None] = mapped_column(String)
    # Compte créé par cette invitation : créateur de projets (invitation admin) ou invité.
    can_create_projects: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (
        Index("ix_benchmark_runs_project_id", "project_id"),
        UniqueConstraint("id", "project_id", name="uq_benchmark_runs_id_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Recherche dont le lot sert de banc d'essai (NULL = recherche initiale).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE")
    )
    # Variantes comparées : [{name, prompt_template_id | system_prompt | None (= généré)}].
    variants: Mapped[list[dict] | None] = mapped_column(JSONB)
    # {use_gold: valider contre le gold, gold_examples: k exemples du gold dans le prompt}
    options: Mapped[dict | None] = mapped_column(JSONB)
    models_tested: Mapped[list[str] | None] = mapped_column(JSONB)
    progress: Mapped[dict | None] = mapped_column(JSONB)  # {done, total} pendant le RUNNING
    status: Mapped[BenchmarkStatus | None] = mapped_column(
        _enum(BenchmarkStatus), server_default="PENDING"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="benchmark_runs")
    results: Mapped[list["BenchmarkResult"]] = relationship(
        back_populates="benchmark_run", passive_deletes=True
    )


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (Index("ix_benchmark_results_benchmark_run_id", "benchmark_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("benchmark_runs.id", ondelete="CASCADE")
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String)  # variante de prompt (NULL = unique)
    n_scored: Mapped[int | None] = mapped_column(Integer)  # articles notés (gold hors exemples)
    # Métriques par item, matrice de confusion, accord entre variantes…
    details: Mapped[dict | None] = mapped_column(JSONB)
    execution_time_seconds: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    precision_score: Mapped[float | None] = mapped_column(Float)
    recall_score: Mapped[float | None] = mapped_column(Float)
    f1_score: Mapped[float | None] = mapped_column(Float)

    benchmark_run: Mapped["BenchmarkRun | None"] = relationship(back_populates="results")


class BenchmarkPrediction(_ProvenanceState, Base):
    """Réponse d'une variante (modèle × prompt) pour un article du banc d'essai : comparaison côte
    à côte, désaccords, recalcul des métriques sans relancer les modèles."""

    __tablename__ = "benchmark_predictions"
    __table_args__ = (
        Index("ix_benchmark_predictions_run_id", "benchmark_run_id"),
        *_source_links("benchmark_predictions", invocation=True),
        ForeignKeyConstraint(
            ["benchmark_run_id", "provenance_project_id"],
            ["benchmark_runs.id", "benchmark_runs.project_id"],
            name="fk_benchmark_predictions_provenance_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    benchmark_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String)  # ex. invalid_json
    seconds: Mapped[float | None] = mapped_column(Float)
    # search_id mirrors the producing invocation for captured rows only.
    search_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    provenance_project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    invocation_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (Index("ix_prompt_templates_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    version_number: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    user_prompt_schema: Mapped[str | None] = mapped_column(Text)
    prompt_type: Mapped[PromptType | None] = mapped_column(
        _enum(PromptType), server_default="EXTRACTION"
    )
    status: Mapped[PromptStatus | None] = mapped_column(_enum(PromptStatus), server_default="DRAFT")

    project: Mapped["Project | None"] = relationship(back_populates="prompt_templates")


class Search(Base):
    """Une recherche : une (ou plusieurs) questions posées à un lot d'articles du projet.

    Elle porte sa propre question, ses items de réponse et ses parties de texte lues ; ses
    prédictions, relectures, analyses et références (gold) lui sont rattachées. Le lot est soit
    tout le corpus (``lot_all``, dynamique), soit une liste figée (``search_articles``), par
    exemple « les articles où la recherche X a répondu oui » (``parent_id`` + ``parent_spec``).
    """

    __tablename__ = "searches"
    __table_args__ = (
        Index("ix_searches_project_id", "project_id"),
        UniqueConstraint("id", "project_id", name="uq_searches_id_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[list[dict] | None] = mapped_column(JSONB)
    text_sections: Mapped[list[str] | None] = mapped_column(JSONB)
    read_windows: Mapped[int | None] = mapped_column(Integer)
    # Empreinte de (question, items) : même empreinte = même question, références partagées.
    definition_hash: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, server_default="standard", default="standard")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    lot_all: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="SET NULL")
    )
    parent_spec: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SearchArticle(Base):
    """Lot figé d'une recherche (absent quand ``Search.lot_all``)."""

    __tablename__ = "search_articles"

    search_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class GoldArticle(Base):
    __tablename__ = "gold_articles"
    __table_args__ = (
        Index("ix_gold_articles_project_id", "project_id"),
        Index("ix_gold_articles_article_id", "article_id"),
        Index("uq_gold_articles_search_article", "search_id", "article_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    ground_truth_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Recherche (donc question) dont c'est la référence ; NULL = recherche par défaut du projet.
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # Relecture d'origine (lien retour vers la fiche) et relecteur ; copie figée des réponses.
    source_annotation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("human_annotations.id", ondelete="SET NULL")
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )

    project: Mapped["Project | None"] = relationship(back_populates="gold_articles")
    article: Mapped["Article | None"] = relationship(back_populates="gold_articles")


class ArticlePrediction(_ProvenanceState, Base):
    __tablename__ = "article_predictions"
    __table_args__ = (
        Index("ix_article_predictions_article_id", "article_id"),
        Index("ix_article_predictions_prompt_template_id", "prompt_template_id"),
        *_source_links("article_predictions", invocation=True),
        CheckConstraint(
            "quote_checks_json IS NULL OR ("
            "source_snapshot_id IS NOT NULL AND invocation_id IS NOT NULL AND "
            "jsonb_typeof(quote_checks_json) = 'object' AND "
            "coalesce(quote_checks_json->>'schema_version','') = 'quote-checks-v1' AND "
            "coalesce(quote_checks_json->>'input_ref','') = invocation_id::text AND "
            "coalesce(quote_checks_json->>'source_snapshot_ref','') = source_snapshot_id::text AND "
            "jsonb_typeof(quote_checks_json->'items') = 'array')",
            name="ck_article_predictions_quote_checks_scope",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "quote_checks_json IS NULL OR ("
            "source_snapshot_id IS NOT NULL AND invocation_id IS NOT NULL AND "
            "json_type(quote_checks_json) = 'object' AND "
            "coalesce(json_extract(quote_checks_json,'$.schema_version'),'') = 'quote-checks-v1' "
            "AND replace(coalesce(json_extract(quote_checks_json,'$.input_ref'),''),'-','') "
            "= invocation_id "
            "AND replace(coalesce(json_extract(quote_checks_json,'$.source_snapshot_ref'),''),"
            "'-','') "
            "= source_snapshot_id AND json_type(quote_checks_json,'$.items') = 'array')",
            name="ck_article_predictions_quote_checks_scope",
        ).ddl_if(dialect="sqlite"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompt_templates.id")
    )
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    evidence_quotes: Mapped[list[str] | None] = mapped_column(JSONB)
    # V1 checks against the final producing invocation's retained target spans.
    # NULL means historical/unversioned; this is not a semantic support judgment.
    quote_checks_json: Mapped[dict | None] = mapped_column(PROVENANCE_JSON)
    provenance_project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    invocation_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Recherche qui l'a produite ; NULL = recherche par défaut du projet (données anciennes).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PredictionStatus | None] = mapped_column(
        _enum(PredictionStatus), server_default="NEEDS_HUMAN_REVIEW"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article | None"] = relationship(
        back_populates="predictions",
        primaryjoin="Article.id == ArticlePrediction.article_id",
        foreign_keys="ArticlePrediction.article_id",
    )


class HumanAnnotation(_ProvenanceState, Base):
    __tablename__ = "human_annotations"
    __table_args__ = (
        Index("ix_human_annotations_article_id", "article_id"),
        Index("ix_human_annotations_user_id", "user_id"),
        *_source_links("human_annotations"),
        CheckConstraint(
            "(source_snapshot_id IS NULL AND source_view_id IS NULL "
            "AND source_view_sha256 IS NULL) "
            "OR (source_snapshot_id IS NOT NULL AND source_view_id IS NOT NULL "
            "AND source_view_sha256 IS NOT NULL)",
            name="ck_human_annotations_source_view",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    annotator_hash: Mapped[str | None] = mapped_column(String)
    provenance_project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_view_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_view_sha256: Mapped[str | None] = mapped_column(String(64))
    # Recherche (question) à laquelle cette relecture répond ; NULL = recherche par défaut.
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # Relecture à l'aveugle : faite sans voir la prédiction du modèle.
    blind: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article | None"] = relationship(
        back_populates="annotations",
        primaryjoin="Article.id == HumanAnnotation.article_id",
        foreign_keys="HumanAnnotation.article_id",
    )
    user: Mapped["User | None"] = relationship(back_populates="annotations")


class SystemSettings(Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint("scope", "project_id", "user_id", "key_name"),
        # La contrainte UNIQUE ci-dessus a des trous NULL (SQLite/PG : NULL ≠ NULL en
        # UNIQUE) : un doublon GLOBAL (project_id=NULL, user_id=NULL) passerait. Les
        # index uniques partiels ci-dessous ferment ces trous, scope par scope.
        Index(
            "uq_settings_global",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
            sqlite_where=text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
        ),
        Index(
            "uq_settings_project",
            "project_id",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'PROJECT' AND user_id IS NULL"),
            sqlite_where=text("scope = 'PROJECT' AND user_id IS NULL"),
        ),
        Index(
            "uq_settings_user",
            "user_id",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'USER' AND project_id IS NULL"),
            sqlite_where=text("scope = 'USER' AND project_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    scope: Mapped[SettingScope] = mapped_column(_enum(SettingScope), server_default="GLOBAL")
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    key_name: Mapped[str] = mapped_column(String, nullable=False)
    value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_secret: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="settings")
    user: Mapped["User | None"] = relationship(back_populates="settings")


class ImportJob(Base):
    """Job d'import PubMed (lot 4A) : persiste la requête + le statut + le résultat.

    MODULE 6 (reproductibilité) : la requête PubMed exacte est conservée ici.
    """

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(String, server_default="import")  # import | fulltext
    job_id: Mapped[str | None] = mapped_column(String)  # id Celery du task
    status: Mapped[ImportJobStatus | None] = mapped_column(
        _enum(ImportJobStatus), server_default="PENDING"
    )
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project | None"] = relationship(back_populates="import_jobs")


class InferenceJobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InferenceJob(Base):
    """Job d'inférence massive (MODULE 4, lot 5) : statut + statistiques clés.

    MODULE 6 (reproductibilité) : le prompt + le modèle utilisés sont conservés.
    Notifications in-app : le frontend poll ``GET /projects/{id}/inference``.
    """

    __tablename__ = "inference_jobs"
    __table_args__ = (
        Index("ix_inference_jobs_project_id", "project_id"),
        UniqueConstraint("id", "project_id", name="uq_inference_jobs_id_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompt_templates.id")
    )
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    # Étage 1 optionnel (encodeur ONNX sur abstracts) ; NULL = pas de pré-filtre.
    prefilter_model: Mapped[str | None] = mapped_column(String)
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # {gold_examples: k exemples du gold dans le prompt, validate_with_gold: bool}
    options: Mapped[dict | None] = mapped_column(JSONB)
    # Reprise d'un job interrompu : les articles déjà prédits depuis cette date sont sautés.
    resume_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[InferenceJobStatus | None] = mapped_column(
        _enum(InferenceJobStatus), server_default="PENDING"
    )
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project | None"] = relationship(back_populates="inference_jobs")


class SourceSnapshot(Base):
    """Immutable captured components/views; optional permitted raw bytes, never a live PDF ref."""

    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "article_id", name="uq_source_snapshots_scope"),
        ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_source_snapshots_article_scope",
        ),
        CheckConstraint("schema_version = 1", name="ck_source_snapshots_version"),
        CheckConstraint(
            "length(canonical_bytes) <= 67108864", name="ck_source_snapshots_canonical_size"
        ),
        CheckConstraint(
            "encode(sha256(canonical_bytes),'hex') = snapshot_sha256 AND "
            "convert_from(canonical_bytes,'UTF8')::jsonb = payload",
            name="ck_source_snapshots_canonical_integrity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "raw_availability IN ('retained','digest_only','not_captured','unavailable')",
            name="ck_source_snapshots_raw_availability",
        ),
        CheckConstraint(
            "(raw_availability = 'retained' AND raw_data IS NOT NULL) OR "
            "(raw_availability <> 'retained' AND raw_data IS NULL)",
            name="ck_source_snapshots_raw_retention",
        ),
        CheckConstraint(
            "raw_data IS NULL OR length(raw_data) <= 26214400",
            name="ck_source_snapshots_raw_size",
        ),
        CheckConstraint(
            "raw_availability NOT IN ('retained','digest_only') OR "
            "(raw_sha256 IS NOT NULL AND length(raw_sha256) = 64 AND raw_media_type IS NOT NULL)",
            name="ck_source_snapshots_raw_digest",
        ),
        CheckConstraint(
            "raw_availability <> 'not_captured' OR raw_sha256 IS NULL",
            name="ck_source_snapshots_not_captured",
        ),
        CheckConstraint(
            "raw_data IS NULL OR encode(sha256(raw_data), 'hex') = raw_sha256",
            name="ck_source_snapshots_raw_bytes_digest",
        ).ddl_if(dialect="postgresql"),
        Index("ix_source_snapshots_article", "project_id", "article_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(PROVENANCE_JSON, nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_availability: Mapped[str] = mapped_column(String, nullable=False)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_media_type: Mapped[str | None] = mapped_column(String)
    raw_data: Mapped[bytes | None] = mapped_column(LargeBinary)


class Invocation(Base):
    """One actual driver call: immutable prepared envelope, separately guarded outcome."""

    __tablename__ = "invocations"
    __table_args__ = (
        UniqueConstraint(
            "id", "source_snapshot_id", "article_id", "group_id", name="uq_invocations_result"
        ),
        UniqueConstraint(
            "id",
            "project_id",
            "article_id",
            "source_snapshot_id",
            "group_id",
            name="uq_invocations_parent_scope",
        ),
        UniqueConstraint("group_id", "sequence", "attempt", name="uq_invocations_attempt"),
        ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_invocations_article_scope",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "project_id", "article_id"],
            ["source_snapshots.id", "source_snapshots.project_id", "source_snapshots.article_id"],
            name="fk_invocations_source_scope",
        ),
        ForeignKeyConstraint(
            ["search_id", "project_id"],
            ["searches.id", "searches.project_id"],
            ondelete="CASCADE",
            name="fk_invocations_search_scope",
        ),
        ForeignKeyConstraint(
            ["inference_job_id", "project_id"],
            ["inference_jobs.id", "inference_jobs.project_id"],
            name="fk_invocations_job_scope",
        ),
        ForeignKeyConstraint(
            ["benchmark_run_id", "project_id"],
            ["benchmark_runs.id", "benchmark_runs.project_id"],
            ondelete="CASCADE",
            name="fk_invocations_run_scope",
        ),
        ForeignKeyConstraint(
            ["parent_id", "project_id", "article_id", "source_snapshot_id", "group_id"],
            [
                "invocations.id",
                "invocations.project_id",
                "invocations.article_id",
                "invocations.source_snapshot_id",
                "invocations.group_id",
            ],
            name="fk_invocations_parent_scope",
        ),
        CheckConstraint("schema_version = 1", name="ck_invocations_version"),
        CheckConstraint(
            "length(canonical_bytes) <= 67108864", name="ck_invocations_canonical_size"
        ),
        CheckConstraint(
            "encode(sha256(canonical_bytes),'hex') = envelope_sha256 AND "
            "convert_from(canonical_bytes,'UTF8')::jsonb = request_envelope",
            name="ck_invocations_canonical_integrity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint("sequence >= 0 AND attempt >= 1", name="ck_invocations_sequence"),
        CheckConstraint("stage IN ('prefilter','main')", name="ck_invocations_stage"),
        CheckConstraint(
            "call_kind IN ('chat','systemone_relevance','systemone_detail',"
            "'systemone_combined','onnx')",
            name="ck_invocations_call_kind",
        ),
        CheckConstraint(
            "inference_job_id IS NULL OR benchmark_run_id IS NULL", name="ck_invocations_job_kind"
        ),
        CheckConstraint(
            "(outcome IN ('prepared','outcome_unknown') AND completed_at IS NULL "
            "AND response_capture IS NULL AND reconciliation IS NULL) OR "
            "(outcome IN ('succeeded','failed') AND completed_at IS NOT NULL "
            "AND completed_at >= prepared_at AND response_capture IS NOT NULL)",
            name="ck_invocations_outcome",
        ),
        Index("ix_invocations_source", "source_snapshot_id"),
        Index("ix_invocations_search", "project_id", "search_id"),
        Index("ix_invocations_job", "inference_job_id"),
        Index("ix_invocations_run", "benchmark_run_id"),
        Index("ix_invocations_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    search_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    inference_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    variant: Mapped[str | None] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    call_kind: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_envelope: Mapped[dict] = mapped_column(PROVENANCE_JSON, nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_capture: Mapped[dict | None] = mapped_column(PROVENANCE_JSON)
    reconciliation: Mapped[dict | None] = mapped_column(PROVENANCE_JSON)


_INVOCATION_FROZEN = (
    "id",
    "project_id",
    "article_id",
    "source_snapshot_id",
    "group_id",
    "parent_id",
    "search_id",
    "inference_job_id",
    "benchmark_run_id",
    "variant",
    "stage",
    "call_kind",
    "sequence",
    "attempt",
    "schema_version",
    "prepared_at",
    "request_sha256",
    "envelope_sha256",
    "request_envelope",
    "canonical_bytes",
)
event.listen(
    ArticlePrediction.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER article_predictions_quote_checks_completed "
        "BEFORE INSERT ON article_predictions WHEN NEW.quote_checks_json IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id "
        "AND i.outcome = 'succeeded') "
        "BEGIN SELECT RAISE(ABORT, 'quotation checks require a completed producing call'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    ArticlePrediction.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER article_predictions_quote_checks_immutable "
        "BEFORE UPDATE ON article_predictions WHEN "
        "NEW.quote_checks_json IS NOT OLD.quote_checks_json OR "
        "(OLD.quote_checks_json IS NOT NULL AND ("
        "NEW.decision_json IS NOT OLD.decision_json OR "
        "NEW.evidence_quotes IS NOT OLD.evidence_quotes)) "
        "BEGIN SELECT RAISE(ABORT, 'quotation checks and checked output are immutable'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    SourceSnapshot.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER source_snapshots_immutable BEFORE UPDATE ON source_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'source snapshots are immutable'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    Invocation.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER invocations_prepared BEFORE INSERT ON invocations WHEN "
        "NEW.outcome <> 'prepared' OR EXISTS (SELECT 1 FROM invocations i WHERE "
        "i.group_id = NEW.group_id AND (i.project_id IS NOT NEW.project_id "
        "OR i.article_id IS NOT NEW.article_id "
        "OR i.source_snapshot_id IS NOT NEW.source_snapshot_id "
        "OR i.search_id IS NOT NEW.search_id OR i.inference_job_id IS NOT NEW.inference_job_id "
        "OR i.benchmark_run_id IS NOT NEW.benchmark_run_id)) "
        "OR (NEW.inference_job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inference_jobs j "
        "WHERE j.id = NEW.inference_job_id AND j.search_id IS NEW.search_id)) "
        "OR (NEW.benchmark_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM benchmark_runs r "
        "WHERE r.id = NEW.benchmark_run_id AND r.search_id IS NEW.search_id)) "
        "BEGIN SELECT RAISE(ABORT, 'invalid prepared invocation or group scope'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    Invocation.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER invocations_response BEFORE UPDATE ON invocations "
        "WHEN NEW.outcome IN ('succeeded','failed') AND ("
        "coalesce(json_type(NEW.response_capture),'') <> 'object' OR "
        "coalesce(json_extract(NEW.response_capture,'$.origin'),'') NOT IN "
        "('driver_raw','driver_parsed','unavailable') OR "
        "coalesce(json_extract(NEW.response_capture,'$.raw_availability'),'') NOT IN "
        "('retained','not_captured','unavailable') OR "
        "(json_extract(NEW.response_capture,'$.origin') <> 'driver_raw' AND ("
        "json_extract(NEW.response_capture,'$.raw_availability') = 'retained' OR "
        "json_extract(NEW.response_capture,'$.raw_base64') IS NOT NULL OR "
        "json_extract(NEW.response_capture,'$.raw_sha256') IS NOT NULL)) OR "
        "(json_extract(NEW.response_capture,'$.origin') = 'driver_parsed' AND "
        "coalesce(json_type(NEW.response_capture,'$.parsed'),'null') = 'null') OR "
        "(json_extract(NEW.response_capture,'$.origin') = 'driver_raw' AND ("
        "coalesce(json_extract(NEW.response_capture,'$.raw_availability'),'') <> 'retained' OR "
        "json_extract(NEW.response_capture,'$.raw_base64') IS NULL OR "
        "length(coalesce(json_extract(NEW.response_capture,'$.raw_sha256'),'')) <> 64)) OR "
        "(json_extract(NEW.response_capture,'$.origin') = 'unavailable' AND ("
        "coalesce(json_extract(NEW.response_capture,'$.reason'),'') = '' OR "
        "coalesce(json_type(NEW.response_capture,'$.parsed'),'null') <> 'null' OR "
        "coalesce(json_type(NEW.response_capture,'$.raw_base64'),'null') <> 'null')) OR "
        "(json_extract(NEW.response_capture,'$.raw_availability') = 'unavailable' AND "
        "coalesce(json_extract(NEW.response_capture,'$.reason'),'') = '')) "
        "BEGIN SELECT RAISE(ABORT, 'invalid actual-response capture'); END"
    ).execute_if(dialect="sqlite"),
)
for _table in (ArticlePrediction.__table__, BenchmarkPrediction.__table__):
    _extra = (
        " OR NOT EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id "
        "AND i.benchmark_run_id = NEW.benchmark_run_id)"
        if _table.name == "benchmark_predictions"
        else " OR EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id "
        "AND i.benchmark_run_id IS NOT NULL)"
    )
    for _action in ("INSERT", "UPDATE"):
        event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER {_table.name}_binding_{_action.lower()} BEFORE {_action} "
                f"ON {_table.name} WHEN NEW.invocation_id IS NOT NULL AND ("
                "NOT EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id "
                "AND i.search_id IS NEW.search_id)" + _extra + ") "
                "BEGIN SELECT RAISE(ABORT, 'producing invocation scope mismatch'); END"
            ).execute_if(dialect="sqlite"),
        )
for _action in ("INSERT", "UPDATE"):
    event.listen(
        HumanAnnotation.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER human_annotations_binding_{_action.lower()} BEFORE {_action} "
            "ON human_annotations WHEN NEW.source_snapshot_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM source_snapshots s, json_each(s.payload,'$.views') v "
            "WHERE s.id = NEW.source_snapshot_id AND "
            "replace(json_extract(v.value,'$.id'),'-','') = NEW.source_view_id "
            "AND json_extract(v.value,'$.sha256') = NEW.source_view_sha256 "
            "AND json_extract(v.value,'$.availability') = 'retained') "
            "BEGIN SELECT RAISE(ABORT, 'review view is unavailable or mismatched'); END"
        ).execute_if(dialect="sqlite"),
    )

for _table in (
    ArticlePrediction.__table__,
    BenchmarkPrediction.__table__,
    HumanAnnotation.__table__,
):
    _binding_columns = ["provenance_project_id", "source_snapshot_id"] + (
        ["source_view_id", "source_view_sha256"]
        if _table.name == "human_annotations"
        else ["invocation_id", "invocation_group_id"]
    )

    _changed = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in _binding_columns)
    _scope = "NEW.article_id IS NOT OLD.article_id OR NEW.search_id IS NOT OLD.search_id"
    if _table.name == "benchmark_predictions":
        _scope += " OR NEW.benchmark_run_id IS NOT OLD.benchmark_run_id"
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_table.name}_binding_immutable BEFORE UPDATE ON {_table.name} "
            f"WHEN ({_changed}) OR (OLD.source_snapshot_id IS NOT NULL AND ({_scope})) "
            "BEGIN SELECT RAISE(ABORT, 'provenance cannot be retargeted or backfilled'); END"
        ).execute_if(dialect="sqlite"),
    )


for _parent, _reference in (
    (InferenceJob.__table__, "inference_job_id"),
    (BenchmarkRun.__table__, "benchmark_run_id"),
):
    event.listen(
        _parent,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_parent.name}_provenance_scope BEFORE UPDATE ON {_parent.name} "
            "WHEN (NEW.project_id IS NOT OLD.project_id OR NEW.search_id IS NOT OLD.search_id) "
            f"AND EXISTS (SELECT 1 FROM invocations i WHERE i.{_reference} = OLD.id) "
            "BEGIN SELECT RAISE(ABORT, 'captured invocation parent scope is immutable'); END"
        ).execute_if(dialect="sqlite"),
    )


@event.listens_for(SourceSnapshot, "before_insert")
def _validate_snapshot_row(_mapper, _connection, target):
    from app.provenance import source_from_record, source_record

    expected = source_record(source_from_record(target), raw_data=target.raw_data)
    if any(
        getattr(target, c.name) != getattr(expected, c.name)
        for c in SourceSnapshot.__table__.columns
        if c.name != "payload"
    ):
        raise ValueError("source row does not match its validated envelope")


@event.listens_for(Invocation, "before_insert")
def _validate_invocation_row(_mapper, connection, target):
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.provenance import (
        invocation_from_record,
        invocation_record,
        prepare_invocation,
        source_from_record,
    )

    payload = (
        connection.execute(
            select(SourceSnapshot.__table__).where(SourceSnapshot.id == target.source_snapshot_id)
        )
        .mappings()
        .one_or_none()
    )
    if payload is None:
        raise ValueError("invocation requires a retained source snapshot")
    envelope = invocation_from_record(target)
    values = envelope.model_dump()
    for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
        values.pop(key)
    checked = prepare_invocation(source_from_record(SimpleNamespace(**payload)), **values)
    expected = invocation_record(checked)
    if any(
        getattr(target, name) != getattr(expected, name)
        for name in _INVOCATION_FROZEN
        if name != "request_envelope"
    ):
        raise ValueError("invocation row does not match its validated envelope")


_frozen_sql = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in _INVOCATION_FROZEN)
event.listen(
    Invocation.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER invocations_immutable BEFORE UPDATE ON invocations WHEN "
        + _frozen_sql
        + " OR OLD.outcome NOT IN ('prepared','outcome_unknown') "
        "OR (OLD.outcome = 'prepared' AND NEW.outcome NOT IN "
        "('succeeded','failed','outcome_unknown')) "
        "OR (OLD.outcome = 'prepared' AND NEW.reconciliation IS NOT NULL) "
        "OR (OLD.outcome = 'outcome_unknown' AND (NEW.outcome NOT IN ('succeeded','failed') "
        "OR NEW.reconciliation IS NULL OR "
        "coalesce(json_extract(NEW.response_capture,'$.origin'),'') NOT IN "
        "('driver_raw','driver_parsed') OR "
        "coalesce(json_extract(NEW.reconciliation,'$.actor_ref'),'') = '' OR "
        "coalesce(json_extract(NEW.reconciliation,'$.reason'),'') = '' OR "
        "coalesce(json_extract(NEW.reconciliation,'$.recorded_at'),'') = '')) "
        "BEGIN SELECT RAISE(ABORT, 'invalid invocation mutation'); END"
    ).execute_if(dialect="sqlite"),
)

# Import all models here so they're registered with Base.metadata
# This ensures SQLAlchemy knows about all tables when creating the schema.
from .site import Site
from .infrastructure import DetectedInfrastructure
from .document import PublicDocument
from .estimate import ResourceEstimate

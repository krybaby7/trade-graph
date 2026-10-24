"""Data-only patches; the model never supplies commands, attestations or authority."""

from pydantic import Field, model_validator

from trade_graph.contracts.models import ContractModel


class ArtifactFile(ContractModel):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=65536)


class EngineerPatch(ContractModel):
    files: list[ArtifactFile] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def bounded_files(self):
        if len({f.path for f in self.files}) != len(self.files):
            raise ValueError("duplicate artifact path")
        if sum(len(f.content.encode()) for f in self.files) > 65536:
            raise ValueError("artifact byte limit exceeded")
        if sum(len(f.content.splitlines()) for f in self.files) > 200:
            raise ValueError("artifact line limit exceeded")
        return self

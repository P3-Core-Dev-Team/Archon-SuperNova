package com.metadata.engine.be.metadata_engine_be.models.dto;

import com.metadata.engine.be.metadata_engine_be.models.SensitiveColumn;
import lombok.Data;
import java.util.List;

@Data
public class SensitiveResponse {
    private String message;
    private List<SensitiveColumn> sensitive_columns;
}

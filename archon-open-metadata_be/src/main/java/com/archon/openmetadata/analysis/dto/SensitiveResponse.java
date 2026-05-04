package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class SensitiveResponse {
    private List<SensitiveColumnDto> sensitive_columns;
}

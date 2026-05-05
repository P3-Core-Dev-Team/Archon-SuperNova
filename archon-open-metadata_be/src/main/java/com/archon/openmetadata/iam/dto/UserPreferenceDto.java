package com.archon.openmetadata.iam.dto;

import java.util.UUID;
import lombok.Data;

@Data
public class UserPreferenceDto {
    private UUID id;
    private String theme;
    private String dateFormat;
    private String timezone;
    private String defaultGraphView;
}

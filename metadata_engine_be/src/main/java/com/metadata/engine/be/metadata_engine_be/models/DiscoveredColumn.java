package com.metadata.engine.be.metadata_engine_be.models;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.Data;

@Data
@Entity
@Table(name = "discovered_columns")
public class DiscoveredColumn {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String columnName;
    private String dataType;
    private Integer length;
    private Boolean isPrimaryKey;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "discovered_table_id")
    @JsonIgnore
    private DiscoveredTable discoveredTable;
}

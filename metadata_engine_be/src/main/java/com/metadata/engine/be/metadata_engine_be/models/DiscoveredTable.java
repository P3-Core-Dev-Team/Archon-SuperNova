package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;

import java.util.List;

@Data
@Entity
@Table(name = "discovered_tables")
public class DiscoveredTable {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_id")
    private Long jobId;

    private String schemaName;
    private String tableName;
    
    @Column(name = "table_type")
    private String tableType;

    @OneToMany(mappedBy = "discoveredTable", cascade = CascadeType.ALL, fetch = FetchType.LAZY)
    private List<DiscoveredColumn> columns;
}

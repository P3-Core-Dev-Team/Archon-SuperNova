package com.metadata.engine.be.metadata_engine_be.models;

import jakarta.persistence.*;
import lombok.Data;

import java.util.List;

@Data
@Entity
@Table(name = "domain_groups")
public class DomainGroup {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_id")
    private Long jobId;

    private String domainName;

    @Transient
    private List<String> tableNames;

    @com.fasterxml.jackson.annotation.JsonIgnoreProperties({"columns", "hibernateLazyInitializer", "handler"})
    @ManyToMany(fetch = FetchType.EAGER)
    @JoinTable(
        name = "domain_group_mappings",
        joinColumns = @JoinColumn(name = "domain_id"),
        inverseJoinColumns = @JoinColumn(name = "table_id")
    )
    private List<DiscoveredTable> tables;
}

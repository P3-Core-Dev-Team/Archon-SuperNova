package com.metadata.engine.be.metadata_engine_be.controllers;

import com.metadata.engine.be.metadata_engine_be.models.DataConnection;
import com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable;
import com.metadata.engine.be.metadata_engine_be.models.DomainGroup;
import com.metadata.engine.be.metadata_engine_be.models.Relationship;
import com.metadata.engine.be.metadata_engine_be.models.SensitiveColumn;
import com.metadata.engine.be.metadata_engine_be.services.AnalysisService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@CrossOrigin(origins = "*")
@RestController
@RequestMapping("/api/analysis")
@RequiredArgsConstructor
public class AnalysisController {

    private final AnalysisService analysisService;

    // Connection Management
    @PostMapping("/connect")
    public DataConnection connect(@RequestBody DataConnection config) {
        return analysisService.testAndSaveConnection(config);
    }

    @GetMapping("/connections")
    public List<DataConnection> getConnections() {
        return analysisService.getConnections();
    }

    // Job Orchestration
    @PostMapping("/trigger")
    public com.metadata.engine.be.metadata_engine_be.models.AnalysisJob triggerJob(@RequestParam String schema) {
        return analysisService.initiateJob(schema);
    }

    @PutMapping("/job/{id}/complete")
    public com.metadata.engine.be.metadata_engine_be.models.AnalysisJob finishJob(@PathVariable Long id, @RequestBody String logs) {
        return analysisService.completeJob(id, logs);
    }
    
    @DeleteMapping("/job/{id}")
    public void deleteJob(@PathVariable Long id) {
        analysisService.deleteJob(id);
    }
    
    @GetMapping(value = "/job/{id}/stream", produces = org.springframework.http.MediaType.TEXT_EVENT_STREAM_VALUE)
    public org.springframework.web.servlet.mvc.method.annotation.SseEmitter streamJob(@PathVariable Long id) {
        return analysisService.subscribe(id);
    }

    @GetMapping("/jobs")
    public List<com.metadata.engine.be.metadata_engine_be.models.AnalysisJob> getAllJobs() {
        return analysisService.getAllJobs();
    }

    // FE Fetch Endpoints
    @GetMapping("/tables/{jobId}")
    public org.springframework.hateoas.PagedModel<org.springframework.hateoas.EntityModel<DiscoveredTable>> getDiscoveredTables(
            @PathVariable Long jobId, 
            org.springframework.data.domain.Pageable pageable, 
            org.springframework.data.web.PagedResourcesAssembler<DiscoveredTable> assembler) {
        return assembler.toModel(analysisService.getDiscoveredTables(jobId, pageable));
    }

    @GetMapping("/relationships/{jobId}")
    public org.springframework.hateoas.PagedModel<org.springframework.hateoas.EntityModel<Relationship>> getRelationships(
            @PathVariable Long jobId, 
            org.springframework.data.domain.Pageable pageable, 
            org.springframework.data.web.PagedResourcesAssembler<Relationship> assembler) {
        return assembler.toModel(analysisService.getRelationships(jobId, pageable));
    }

    @GetMapping("/domains/{jobId}")
    public org.springframework.hateoas.PagedModel<org.springframework.hateoas.EntityModel<DomainGroup>> getDomainGroups(
            @PathVariable Long jobId, 
            org.springframework.data.domain.Pageable pageable, 
            org.springframework.data.web.PagedResourcesAssembler<DomainGroup> assembler) {
        return assembler.toModel(analysisService.getDomainGroups(jobId, pageable));
    }

    @GetMapping("/sensitive/{jobId}")
    public org.springframework.hateoas.PagedModel<org.springframework.hateoas.EntityModel<SensitiveColumn>> getSensitiveColumns(
            @PathVariable Long jobId, 
            org.springframework.data.domain.Pageable pageable, 
            org.springframework.data.web.PagedResourcesAssembler<SensitiveColumn> assembler) {
        return assembler.toModel(analysisService.getSensitiveColumns(jobId, pageable));
    }

    // Python Ingestion Endpoints
    @PostMapping("/tables")
    public List<DiscoveredTable> saveDiscoveredTables(@RequestBody List<DiscoveredTable> tables) {
        return analysisService.saveDiscoveredTables(tables);
    }

    @PostMapping("/relationships")
    public List<Relationship> saveRelationships(@RequestBody List<Relationship> relationships) {
        return analysisService.saveRelationships(relationships);
    }

    @PostMapping("/domains")
    public List<DomainGroup> saveDomainGroups(@RequestBody List<DomainGroup> domains) {
        return analysisService.saveDomainGroups(domains);
    }

    @PostMapping("/sensitive")
    public List<SensitiveColumn> saveSensitiveColumns(@RequestBody List<SensitiveColumn> columns) {
        return analysisService.saveSensitiveColumns(columns);
    }
}

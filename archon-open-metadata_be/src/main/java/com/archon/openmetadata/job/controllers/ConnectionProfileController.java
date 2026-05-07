package com.archon.openmetadata.job.controllers;

import com.archon.openmetadata.job.dto.ConnectionProfileDto;
import com.archon.openmetadata.job.dto.ConnectionProfileFilterBean;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.services.ConnectionProfileService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import com.archon.openmetadata.common.repositories.SystemAuditLogRepository;
import com.archon.openmetadata.common.models.SystemAuditLog;
import java.time.LocalDateTime;

@RestController
@RequestMapping("/api/v1/connection-profiles")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class ConnectionProfileController {

  private final ConnectionProfileService service;
  private final ModelMapper modelMapper;
  private final SystemAuditLogRepository auditRepo;
  private final PagedResourcesAssembler<ConnectionProfile> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<ConnectionProfileDto>>> fetchAll(Pageable pageable) {
    Page<ConnectionProfile> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, ConnectionProfileDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ConnectionProfileController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<ConnectionProfileDto>> getById(@PathVariable UUID id) {
    ConnectionProfile entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, ConnectionProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ConnectionProfileController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<ConnectionProfileDto>> create(
      @RequestBody ConnectionProfileDto dto) {
    ConnectionProfile entity = modelMapper.map(dto, ConnectionProfile.class);
    entity.setDbType(dto.getDbType());
    entity.setHost(dto.getHost());
    entity.setPort(dto.getPort());
    entity.setDatabaseName(dto.getDatabaseName());
    
    if (dto.getDbType() != null && dto.getHost() != null && dto.getPort() != null && dto.getDatabaseName() != null) {
        String url = dto.getDbType().generateUrl(dto.getHost(), dto.getPort(), dto.getDatabaseName());
        entity.setUrl(url);
    }

    ConnectionProfile saved = service.save(entity);
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, ConnectionProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ConnectionProfileController.class)
                        .getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<ConnectionProfileDto>> update(
      @PathVariable UUID id, @RequestBody ConnectionProfileDto dto) {
    dto.setId(id);
    ConnectionProfile entity = modelMapper.map(dto, ConnectionProfile.class);
    entity.setDbType(dto.getDbType());
    entity.setHost(dto.getHost());
    entity.setPort(dto.getPort());
    entity.setDatabaseName(dto.getDatabaseName());
    
    if (dto.getDbType() != null && dto.getHost() != null && dto.getPort() != null && dto.getDatabaseName() != null) {
        String url = dto.getDbType().generateUrl(dto.getHost(), dto.getPort(), dto.getDatabaseName());
        entity.setUrl(url);
    }

    ConnectionProfile updated = service.save(entity);
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, ConnectionProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ConnectionProfileController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<ConnectionProfileDto>>> searchAll(
      @RequestBody ConnectionProfileFilterBean filterBean, Pageable pageable) {
    Page<ConnectionProfile> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, ConnectionProfileDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ConnectionProfileController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @PostMapping("/test")
  public ResponseEntity<java.util.Map<String, Object>> testConnection(@RequestBody ConnectionProfileDto dto) {
    boolean success = service.testConnection(dto);
    java.util.Map<String, Object> response = new java.util.HashMap<>();
    response.put("success", success);
    response.put("message", success ? "Connection successful!" : "Connection failed!");
    return ResponseEntity.ok(response);
  }
}

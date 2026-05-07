package com.archon.openmetadata.job.controllers;

import com.archon.openmetadata.job.dto.JobDto;
import com.archon.openmetadata.job.dto.JobFilterBean;
import com.archon.openmetadata.job.models.Job;
import com.archon.openmetadata.job.services.JobService;
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
import org.springframework.beans.factory.annotation.Autowired;
import java.time.LocalDateTime;

@RestController
@RequestMapping("/api/v1/jobs")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class JobController {

  private final JobService service;
  private final ModelMapper modelMapper;
  private final SystemAuditLogRepository auditRepo;
  private final PagedResourcesAssembler<Job> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<JobDto>>> fetchAll(Pageable pageable) {
    Page<Job> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, JobDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobController.class).getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<JobDto>> getById(@PathVariable UUID id) {
    Job entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, JobDto.class),
            WebMvcLinkBuilder.linkTo(WebMvcLinkBuilder.methodOn(JobController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<JobDto>> create(@RequestBody JobDto dto) {
    Job saved = service.save(modelMapper.map(dto, Job.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, JobDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<JobDto>> update(
      @PathVariable UUID id, @RequestBody JobDto dto) {
    dto.setId(id);
    Job updated = service.save(modelMapper.map(dto, Job.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, JobDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobController.class).getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<JobDto>>> searchAll(
      @RequestBody JobFilterBean filterBean, Pageable pageable) {
    Page<Job> page =
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
                    modelMapper.map(entity, JobDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobController.class).getById(entity.getId()))
                        .withSelfRel())));
  }

  @Autowired
  private com.archon.openmetadata.job.services.SseBroadcasterService sseBroadcasterService;

  @GetMapping(value = "/{id}/stream", produces = org.springframework.http.MediaType.TEXT_EVENT_STREAM_VALUE)
  public org.springframework.web.servlet.mvc.method.annotation.SseEmitter streamJob(@PathVariable UUID id) {
    return sseBroadcasterService.createEmitter(id);
  }
}

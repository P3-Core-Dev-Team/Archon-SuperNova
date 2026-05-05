package com.archon.openmetadata.job.controllers;

import com.archon.openmetadata.job.dto.JobTemplateOptionRuleDto;
import com.archon.openmetadata.job.dto.JobTemplateOptionRuleFilterBean;
import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import com.archon.openmetadata.job.services.JobTemplateOptionRuleService;
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

@RestController
@RequestMapping("/api/v1/jobtemplateoptionrules")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class JobTemplateOptionRuleController {

  private final JobTemplateOptionRuleService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<JobTemplateOptionRule> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<JobTemplateOptionRuleDto>>> fetchAll(
      Pageable pageable) {
    Page<JobTemplateOptionRule> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, JobTemplateOptionRuleDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobTemplateOptionRuleController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<JobTemplateOptionRuleDto>> getById(@PathVariable UUID id) {
    JobTemplateOptionRule entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, JobTemplateOptionRuleDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateOptionRuleController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<JobTemplateOptionRuleDto>> create(
      @RequestBody JobTemplateOptionRuleDto dto) {
    JobTemplateOptionRule saved = service.save(modelMapper.map(dto, JobTemplateOptionRule.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, JobTemplateOptionRuleDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateOptionRuleController.class)
                        .getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<JobTemplateOptionRuleDto>> update(
      @PathVariable UUID id, @RequestBody JobTemplateOptionRuleDto dto) {
    dto.setId(id);
    JobTemplateOptionRule updated = service.save(modelMapper.map(dto, JobTemplateOptionRule.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, JobTemplateOptionRuleDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateOptionRuleController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<JobTemplateOptionRuleDto>>> searchAll(
      @RequestBody JobTemplateOptionRuleFilterBean filterBean, Pageable pageable) {
    Page<JobTemplateOptionRule> page =
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
                    modelMapper.map(entity, JobTemplateOptionRuleDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobTemplateOptionRuleController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}

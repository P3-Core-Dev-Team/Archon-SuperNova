package com.archon.openmetadata.metadata.controllers;

import com.archon.openmetadata.metadata.dto.RelationshipDto;
import com.archon.openmetadata.metadata.dto.RelationshipFilterBean;
import com.archon.openmetadata.metadata.models.Relationship;
import com.archon.openmetadata.metadata.services.RelationshipService;
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
@RequestMapping("/api/v1/relationships")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class RelationshipController {

  private final RelationshipService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<Relationship> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<RelationshipDto>>> fetchAll(Pageable pageable) {
    Page<Relationship> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, RelationshipDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(RelationshipController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<RelationshipDto>> getById(@PathVariable UUID id) {
    Relationship entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, RelationshipDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(RelationshipController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<RelationshipDto>> create(@RequestBody RelationshipDto dto) {
    Relationship saved = service.save(modelMapper.map(dto, Relationship.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, RelationshipDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(RelationshipController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<RelationshipDto>> update(
      @PathVariable UUID id, @RequestBody RelationshipDto dto) {
    dto.setId(id);
    Relationship updated = service.save(modelMapper.map(dto, Relationship.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, RelationshipDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(RelationshipController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<RelationshipDto>>> searchAll(
      @RequestBody RelationshipFilterBean filterBean, Pageable pageable) {
    Page<Relationship> page =
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
              if (filterBean.getJobId() != null) {
                predicates.add(criteriaBuilder.equal(root.get("job").get("id"), filterBean.getJobId()));
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
                    modelMapper.map(entity, RelationshipDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(RelationshipController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
